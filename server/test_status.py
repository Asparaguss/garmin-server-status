import http.client
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from caddy_config import ACME, address, plan
from status_service import ActivatedServer, Sampler, cpu_counters, read_metrics

TOKEN = "a" * 64
INSTANCE = "test-instance"
EXISTING = """{$MAP_DOMAIN}, sibling.example.org {
    tls /certs/fullchain.pem /certs/privkey.pem
    handle /maps {
        respond "OpenMaps"
    }
}
"""


class CaddyPlanTests(unittest.TestCase):
    def test_existing_certificate_and_routes_are_preserved(self):
        env = {"MAP_DOMAIN": "openmaps.example.org"}
        config = plan(EXISTING, env["MAP_DOMAIN"], "/watch/status", "127.0.0.1:8768", env, True)
        self.assertIn("tls /certs/fullchain.pem /certs/privkey.pem", config)
        self.assertIn('respond "OpenMaps"', config)
        self.assertIn("host openmaps.example.org", config)
        self.assertIn("path /watch/status", config)
        self.assertNotIn(ACME, config)
        self.assertEqual(config, plan(config, env["MAP_DOMAIN"], "/watch/status", "127.0.0.1:8768", env, True))

    def test_new_site_default_path_and_repeat_after_issuance(self):
        domain, path = address("Status.Example.org", "")
        config = plan(EXISTING, domain, path, "unix//data/.garmin-server-status.sock", {}, False)
        self.assertTrue(config.startswith(EXISTING))
        self.assertIn("path /status", config)
        self.assertIn(f"issuer acme {ACME}", config)
        self.assertIn("reverse_proxy unix//data/.garmin-server-status.sock", config)
        self.assertEqual(config, plan(config, domain, path, "unix//data/.garmin-server-status.sock", {}, True))
        updated = plan(config, domain, "/metrics/watch", "unix//data/.garmin-server-status.sock", {}, True)
        self.assertNotIn("path /status", updated)
        self.assertIn("path /metrics/watch", updated)

    def test_reject_unsafe_path_or_collision_with_unmanaged_https_site(self):
        for bad in ["/", "/../status", "/status?token=x", "/status*", "/x//y"]:
            with self.subTest(path=bad), self.assertRaises(ValueError):
                address("status.example.org", bad)
        with self.assertRaises(ValueError):
            plan(EXISTING, "elsewhere.example.org", "/status", "127.0.0.1:8768", {}, True)


def test_reading():
    return {"version": 1, "cpu": 24, "ram": 57, "disk": 68, "load": [42, 36, 29],
            "uptime_s": 1051200, "sampled_at": int(time.time())}


def request(connection, path="/metrics", token=None, method="GET"):
    try:
        headers = {"Authorization": "Bearer " + token} if token else {}
        connection.request(method, path, headers=headers)
        response = connection.getresponse()
        return response.status, response.getheader("X-Garmin-Status"), response.read()
    finally:
        connection.close()


class TestBackend(unittest.TestCase):
    def test_linux_counters(self):
        if not hasattr(os, "statvfs"):
            self.skipTest("Requires Linux /proc and statvfs")
        proc = Path("/proc")
        before = cpu_counters(proc)
        time.sleep(0.1)
        metrics = read_metrics(proc, "/", before, cpu_counters(proc))
        self.assertEqual(3, len(metrics["load"]))
        self.assertGreater(metrics["uptime_s"], 0)
        for metric in ("cpu", "ram", "disk"):
            self.assertTrue(0 <= metrics[metric] <= 100)

    def test_authenticated_read_only_http(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            self.assert_auth(listener)

    @unittest.skipUnless(os.name == "posix", "Requires Linux Unix sockets")
    def test_unix_socket_for_docker_proxy(self):
        with tempfile.TemporaryDirectory() as temp, socket.socket(socket.AF_UNIX) as listener:
            listener.bind(str(Path(temp) / "status.sock"))
            listener.listen()
            self.assert_auth(listener)

    def assert_auth(self, listener):
        sampler = Sampler()
        sampler.sample = (time.monotonic(), test_reading())
        server = ActivatedServer(listener, sampler, TOKEN.encode(), INSTANCE)
        worker = threading.Thread(target=server.serve_forever)
        worker.start()
        try:
            location = listener.getsockname()

            def connect():
                if isinstance(location, tuple):
                    return http.client.HTTPConnection(*location, timeout=3)
                connection = http.client.HTTPConnection("localhost", timeout=3)
                client = socket.socket(socket.AF_UNIX)
                client.settimeout(3)
                client.connect(location)
                connection.sock = client
                return connection

            self.assertEqual((401, INSTANCE), request(connect())[:2])
            self.assertEqual(401, request(connect(), token="revoked")[0])
            self.assertEqual(404, request(connect(), path="/unrelated", token=TOKEN)[0])
            self.assertEqual(405, request(connect(), method="POST", token=TOKEN)[0])
            status, marker, body = request(connect(), token=TOKEN)
            self.assertEqual((200, INSTANCE), (status, marker))
            self.assertEqual(24, json.loads(body)["cpu"])
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=3)


@unittest.skipUnless(os.name == "posix", "Requires Linux installer support")
class InstallTests(unittest.TestCase):
    def test_matching_catch_all_401_requires_explicit_confirmation(self):
        import install

        endpoint = "https://maps.example.org/status"
        with patch.object(install, "public_request", return_value=(401, None, b"Unauthorized")) as request:
            with patch.object(install, "ask", return_value="yes"):
                install.check_existing_path(endpoint, INSTANCE)
            self.assertTrue(request.call_args.args[0].startswith("https://maps.example.org/garmin-status-probe-"))
            with patch.object(install, "ask", return_value="no"), self.assertRaises(ValueError):
                install.check_existing_path(endpoint, INSTANCE)
        with (
            patch.object(install, "public_request", side_effect=[(401, None, b"Protected"), (404, None, b"Missing")]),
            patch.object(install, "ask") as prompt,
            self.assertRaises(ValueError),
        ):
            install.check_existing_path(endpoint, INSTANCE)
        prompt.assert_not_called()

    def test_compose_caddy_is_detected_and_selected_by_default(self):
        import install

        listing = "\n".join(json.dumps(record) for record in (
            {"Names": "maps-caddy-1", "Image": "local/proxy:stable",
             "Labels": "com.docker.compose.project=maps,com.docker.compose.service=caddy"},
            {"Names": "maps-api-1", "Image": "local/maps:stable",
             "Labels": "com.docker.compose.service=api"},
        ))
        with (
            patch.object(install.shutil, "which", side_effect=lambda name: "/usr/bin/docker" if name == "docker" else None),
            patch.object(install, "run", return_value=listing),
            patch.object(install, "ask", side_effect=lambda _, default="": default),
        ):
            self.assertEqual(("docker", "maps-caddy-1", "/etc/caddy/Caddyfile"),
                             install.choose_proxy({}))

    def test_multiple_docker_caddy_containers_require_a_selection(self):
        import install

        with (
            patch.object(install, "running_caddy_containers", return_value=["maps-caddy-1", "proxy"]),
            patch.object(install, "active_native_caddy", return_value=False),
            patch.object(install, "ask", side_effect=["docker", "proxy", "/etc/caddy/Caddyfile"]) as prompts,
        ):
            self.assertEqual(("docker", "proxy", "/etc/caddy/Caddyfile"), install.choose_proxy({}))
            self.assertEqual(("Running Caddy container", ""), prompts.call_args_list[1].args)

    def test_docker_file_bind_and_persistent_data_mount(self):
        from install import Proxy

        mounts = [
            {"Type": "bind", "Source": "/srv/caddy", "Destination": "/etc/caddy"},
            {"Type": "bind", "Source": "/srv/openmaps/Caddyfile", "Destination": "/etc/caddy/Caddyfile"},
            {"Type": "volume", "Source": "/var/lib/docker/volumes/caddy_data/_data", "Destination": "/data"},
        ]
        self.assertEqual(Path("/srv/openmaps/Caddyfile"), Proxy.host_path(mounts, "/etc/caddy/Caddyfile"))
        self.assertEqual(Path("/var/lib/docker/volumes/caddy_data/_data/.garmin-server-status.sock"),
                         Proxy.host_path(mounts, "/data/.garmin-server-status.sock"))

    def test_https_failure_restores_previous_proxy_config(self):
        import install

        class FakeProxy:
            listener = upstream = "127.0.0.1:8768"
            source = "/etc/caddy/Caddyfile"
            environment = {}

            def __init__(self, config):
                self.config = config
                self.reloads = []

            def adapt(self, _text):
                return {}

            def validate(self, text):
                assert "path /status" in text

            def replace(self, expected, new):
                assert self.config.read_text() == expected
                self.config.write_text(new)

            def apply(self, restart):
                self.reloads.append((restart, self.config.read_text()))

        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            original = 'openmaps.example.org {\n  respond /maps "OpenMaps"\n}\n'
            config = state / "Caddyfile"
            config.write_text(original)
            proxy = FakeProxy(config)
            with (
                patch.object(install, "STATE", state),
                patch.object(install, "Proxy", return_value=proxy),
                patch.object(install, "ask", side_effect=lambda _, default="": default or "status.example.org"),
                patch.object(install.socket, "getaddrinfo"),
                patch.object(install, "has_valid_tls", return_value=False),
                patch.object(install, "install_service"),
                patch.object(install, "run"),
                patch.object(install, "verify", side_effect=RuntimeError("test challenge failure")),
                self.assertRaisesRegex(RuntimeError, "test challenge failure"),
            ):
                install.install()
            self.assertEqual(original, config.read_text())
            self.assertEqual(original, (state / "Caddyfile.previous").read_text())
            self.assertFalse((state / "install.json").exists())
            self.assertEqual(2, len(proxy.reloads))
            self.assertIn("path /status", proxy.reloads[0][1])
            self.assertEqual((False, original), proxy.reloads[1])


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


@unittest.skipUnless(os.getenv("CADDY_TEST_BINARY"), "Set CADDY_TEST_BINARY to run real Caddy integration")
class RealCaddyTests(unittest.TestCase):
    def test_validated_reload_keeps_routes_and_local_certificate(self):
        caddy = os.environ["CADDY_TEST_BINARY"]
        with tempfile.TemporaryDirectory() as temp, socket.socket() as listener:
            root = Path(temp)
            https_port, admin_port = free_port(), free_port()
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            upstream = f"127.0.0.1:{listener.getsockname()[1]}"
            original = (
                f"{{\n admin 127.0.0.1:{admin_port}\n https_port {https_port}\n"
                " auto_https disable_redirects\n skip_install_trust\n}\n"
                "status.localhost {\n bind 127.0.0.1\n tls internal\n"
                ' handle /maps {\n  respond "OpenMaps"\n }\n'
                ' handle {\n  respond "missing" 404\n }\n}\n'
            )
            config = root / "Caddyfile"
            config.write_text(original)
            env = dict(os.environ, APPDATA=temp, XDG_DATA_HOME=temp, XDG_CONFIG_HOME=temp)

            def caddy_command(*args, input=None):
                result = subprocess.run([caddy, *args], env=env, cwd=temp, input=input, text=True,
                                        capture_output=True, timeout=15)
                self.assertEqual(0, result.returncode, result.stderr)
                return result.stdout

            new_site = plan(original, "second.example.org", "/status", upstream, {}, False)
            caddy_command("validate", "--adapter", "caddyfile", "--config", "-", input=new_site)
            caddy_command("validate", "--adapter", "caddyfile", "--config", "-", input=original)
            with (root / "caddy.log").open("w") as log:
                proxy = subprocess.Popen([caddy, "run", "--config", str(config), "--adapter", "caddyfile"],
                                         env=env, cwd=temp, stdout=log, stderr=log)
                sampler = Sampler()
                sampler.sample = (time.monotonic(), test_reading())
                server = ActivatedServer(listener, sampler, TOKEN.encode(), INSTANCE)
                worker = threading.Thread(target=server.serve_forever)
                worker.start()
                try:
                    deadline = time.monotonic() + 10
                    certificate = None
                    while time.monotonic() < deadline:
                        certificate = next(root.rglob("root.crt"), None)
                        if certificate:
                            break
                        time.sleep(0.1)
                    self.assertIsNotNone(certificate)
                    context = ssl.create_default_context(cafile=str(certificate))

                    def connect():
                        client = http.client.HTTPSConnection("status.localhost", https_port, context=context,
                                                             timeout=3)
                        client._create_connection = lambda _address, timeout, *_: socket.create_connection(
                            ("127.0.0.1", https_port), timeout
                        )
                        return client

                    for attempt in range(20):
                        try:
                            client = connect()
                            client.connect()
                            break
                        except OSError:
                            if attempt == 19:
                                raise
                            time.sleep(0.2)
                    cert_before = client.sock.getpeercert(binary_form=True)
                    client.close()
                    self.assertEqual(b"OpenMaps", request(connect(), "/maps")[2])

                    for path in ("/status", "/monitor/watch"):
                        updated = plan(config.read_text(), "status.localhost", path, upstream, {}, True)
                        caddy_command("validate", "--adapter", "caddyfile", "--config", "-", input=updated)
                        config.write_text(updated)
                        caddy_command("reload", "--adapter", "caddyfile", "--config", str(config))
                        self.assertEqual(b"OpenMaps", request(connect(), "/maps")[2])
                        self.assertEqual((401, INSTANCE), request(connect(), path)[:2])
                        sampler.sample = (time.monotonic(), test_reading())
                        self.assertEqual((200, INSTANCE), request(connect(), path, TOKEN)[:2])
                        client = connect()
                        client.connect()
                        self.assertEqual(cert_before, client.sock.getpeercert(binary_form=True))
                        client.close()
                    self.assertEqual(404, request(connect(), "/status")[0])
                finally:
                    proxy.terminate()
                    proxy.wait(timeout=8)
                    server.shutdown()
                    server.server_close()
                    worker.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
