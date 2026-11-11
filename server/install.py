#!/usr/bin/env python3
"""Install Garmin Server Status on a systemd Linux host with Caddy."""

import argparse
import fcntl
import http.client
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shlex
import shutil
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request

from caddy_config import address, plan

APP = Path("/opt/garmin-server-status")
STATE = Path("/etc/garmin-server-status")
SYSTEMD = Path("/etc/systemd/system")
UNIT = "garmin-server-status"
MARKER_HEADER = "X-Garmin-Status"
SERVICE = """[Unit]
Description=Garmin Server Status metrics

[Service]
ExecStart={python} /opt/garmin-server-status/status_service.py
DynamicUser=yes
LoadCredential=token:/etc/garmin-server-status/token
LoadCredential=instance:/etc/garmin-server-status/instance
NoNewPrivileges=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectSystem=strict
ProtectHome=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
RestrictSUIDSGID=yes
UMask=0077
"""


def run(*args, content=None, cwd=None):
    result = subprocess.run(args, input=content, cwd=cwd, text=True, encoding="utf-8",
                            capture_output=True, timeout=60)
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        raise RuntimeError(f"{shlex.join(args)} failed\n{detail}")
    return result.stdout


def ask(label, default=""):
    suffix = f" [{default}]" if default else ""
    return input(label + suffix + ": ").strip() or default


def active_native_caddy():
    if not shutil.which("caddy"):
        return False
    try:
        run("systemctl", "is-active", "caddy.service")
    except (OSError, RuntimeError, subprocess.SubprocessError):
        return False
    return True


def running_caddy_containers():
    if not shutil.which("docker"):
        return []
    try:
        listing = run("docker", "ps", "--format", "{{json .}}")
    except (OSError, RuntimeError, subprocess.SubprocessError):
        return []
    names = set()
    for line in listing.splitlines():
        entry = json.loads(line)
        labels = entry.get("Labels", "").split(",")
        image = entry.get("Image", "").rsplit("/", 1)[-1].split("@", 1)[0].split(":", 1)[0]
        if image == "caddy" or "com.docker.compose.service=caddy" in labels:
            names.add(entry["Names"])
    return sorted(names)


def choose_proxy(prior):
    candidates = running_caddy_containers()
    if candidates:
        print("Running Docker Caddy containers: " + ", ".join(candidates))
    default_mode = prior.get("mode") or ("docker" if candidates and not active_native_caddy() else "native")
    mode = ask("Caddy setup (native or docker)", default_mode).lower()
    if mode not in {"native", "docker"}:
        raise ValueError("Choose native or docker")
    container = ""
    if mode == "docker":
        default_container = prior.get("container") or (candidates[0] if len(candidates) == 1 else "")
        container = ask("Running Caddy container", default_container)
    config = ask("Active Caddyfile path", prior.get("source", "/etc/caddy/Caddyfile"))
    return mode, container, config


def write(path, content, mode=0o644):
    staged = path.with_name("." + path.name + "." + secrets.token_hex(6))
    fd = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(staged, path)
    finally:
        staged.unlink(missing_ok=True)


class Proxy:
    def __init__(self, mode, container="", config=None):
        self.mode = mode
        self.container = container
        self.environment = dict(os.environ)
        if mode == "native":
            if not shutil.which("caddy"):
                raise ValueError("No native Caddy executable found. For Caddy in Docker Compose, rerun and choose "
                                 "docker. For native Caddy, install and start its systemd package first.")
            self.config = Path(config or "/etc/caddy/Caddyfile").resolve(strict=True)
            self.source = str(self.config)
            self.cwd = self.config.parent
            self.prefix = ("caddy",)
            self.listener = "127.0.0.1:8768"
            self.upstream = self.listener
            run("systemctl", "is-active", "caddy.service")
        else:
            if not shutil.which("docker"):
                raise ValueError("Install the Docker CLI and start the local Linux Docker engine first")
            if run("docker", "info", "--format", "{{.OSType}}").strip() != "linux":
                raise ValueError("Select a local Linux Docker engine")
            if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", container):
                raise ValueError("Enter a running Caddy container name")
            record = json.loads(run("docker", "inspect", "--type", "container", container))[0]
            if not record["State"]["Running"]:
                raise ValueError("Start the Caddy container first")
            self.environment = dict(value.split("=", 1) for value in record["Config"].get("Env", [])
                                    if "=" in value)
            self.source = str(PurePosixPath(config or "/etc/caddy/Caddyfile"))
            if not self.source.startswith("/") or ".." in PurePosixPath(self.source).parts:
                raise ValueError("Enter the absolute Caddyfile path inside the container")
            self.config = self.host_path(record["Mounts"], self.source).resolve(strict=True)
            self.listener = str(self.host_path(record["Mounts"], "/data/.garmin-server-status.sock"))
            if not re.fullmatch(r"/[a-zA-Z0-9_./-]+", self.listener) or len(os.fsencode(self.listener)) > 107:
                raise ValueError("The Docker /data mount path cannot be used for a Linux Unix socket")
            if not Path(self.listener).parent.is_dir():
                raise ValueError("The Docker /data mount must be accessible on the local host")
            self.upstream = "unix//data/.garmin-server-status.sock"
            self.prefix = ("docker", "exec", "-i", "-w", str(PurePosixPath(self.source).parent),
                           container, "caddy")
            self.cwd = None
            run(*self.prefix, "version")
            inside = run("docker", "exec", container, "cat", self.source)
            if inside != self.config.read_text(encoding="utf-8"):
                raise ValueError("The host file and the container's active Caddyfile differ")
        if not self.config.is_file():
            raise ValueError("The Caddyfile must be a regular file")

    @staticmethod
    def host_path(mounts, target):
        desired = PurePosixPath(target)
        for mount in sorted(mounts, key=lambda item: len(item["Destination"]), reverse=True):
            root = PurePosixPath(mount["Destination"])
            if desired == root or root in desired.parents:
                if mount["Type"] not in {"bind", "volume"}:
                    break
                return Path(mount["Source"]).joinpath(*desired.relative_to(root).parts)
        raise ValueError(f"The Caddy container needs a persistent host mount covering {target}")

    def adapt(self, text):
        return json.loads(run(*self.prefix, "adapt", "--adapter", "caddyfile", "--config", "-",
                              content=text, cwd=self.cwd))

    def validate(self, text):
        run(*self.prefix, "validate", "--adapter", "caddyfile", "--config", "-",
            content=text, cwd=self.cwd)

    def apply(self, restart):
        if self.mode == "native":
            run("systemctl", "restart" if restart else "reload", "caddy.service")
        elif restart:
            run("docker", "restart", self.container)
        else:
            run(*self.prefix, "reload", "--adapter", "caddyfile", "--config", self.source)

    def replace(self, expected, new):
        # Keep the same inode for single-file Docker bind mounts.
        with self.config.open("r+", encoding="utf-8", newline="") as stream:
            if stream.read() != expected:
                raise RuntimeError("Caddyfile changed during setup. Review it and rerun the installer.")
            try:
                stream.seek(0)
                stream.write(new)
                stream.truncate()
                stream.flush()
                os.fsync(stream.fileno())
            except OSError:
                stream.seek(0)
                stream.write(expected)
                stream.truncate()
                stream.flush()
                os.fsync(stream.fileno())
                raise


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())


def public_request(url, token=None):
    headers = {"Accept": "application/json"}
    if token is not None:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(url, headers=headers)
    try:
        response = OPENER.open(request, timeout=6)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.code, response.headers.get(MARKER_HEADER), response.read(4096)


def has_valid_tls(domain):
    try:
        with socket.create_connection((domain, 443), timeout=5) as tcp:
            with ssl.create_default_context().wrap_socket(tcp, server_hostname=domain):
                return True
    except OSError:
        return False


def check_existing_path(endpoint, instance):
    code, marker, body = public_request(endpoint)
    if code == 404 or marker == instance:
        return
    if code == 401 and marker is None:
        parent = endpoint.rstrip("/").rsplit("/", 1)[0]
        trailing_slash = "/" if endpoint.endswith("/") else ""
        probe = f"{parent}/garmin-status-probe-{secrets.token_hex(12)}{trailing_slash}"
        if public_request(probe) == (401, None, body):
            print(f"{endpoint} returned the same HTTP 401 as a random path. "
                  "The proxy may require authentication for unknown paths.")
            if ask("Use this exact path for Garmin Server Status? (yes/no)", "no").lower() == "yes":
                return
    raise ValueError(f"{endpoint} already returns HTTP {code}. Choose an unused exact path.")


def verify(url, instance, token):
    deadline = time.monotonic() + 120
    last = "waiting for HTTPS"
    while time.monotonic() < deadline:
        try:
            code, marker, _ = public_request(url)
            last = f"anonymous HTTPS returned HTTP {code}"
            if code == 401 and marker == instance:
                code, marker, body = public_request(url, token)
                last = f"authenticated HTTPS returned HTTP {code}"
                if code == 200 and marker == instance and json.loads(body).get("version") == 1:
                    return
        except (OSError, ValueError) as error:
            last = str(error)
        time.sleep(3)
    raise RuntimeError(
        f"HTTPS verification failed for {url} ({last}). "
        "Check the hostname's public A/AAAA records, inbound TCP 80/443, CAA restrictions, "
        "Caddy's certificate challenge logs, and whether this server can reach its own public hostname."
    )


def install_service(listener):
    interpreter = sys.executable
    if not re.fullmatch(r"/[a-zA-Z0-9_./-]+", interpreter):
        raise ValueError("Use a system Python interpreter with an absolute path without spaces")
    APP.mkdir(mode=0o755, parents=True, exist_ok=True)
    source = Path(__file__).with_name("status_service.py")
    write(APP / source.name, source.read_text(encoding="utf-8"))
    write(SYSTEMD / f"{UNIT}.service", SERVICE.format(python=interpreter))
    socket_unit = (
        "[Unit]\nDescription=Private Garmin Server Status listener\n\n"
        f"[Socket]\nListenStream={listener}\nSocketMode=0666\nRemoveOnStop=yes\n\n"
        "[Install]\nWantedBy=sockets.target\n"
    )
    write(SYSTEMD / f"{UNIT}.socket", socket_unit)
    run("systemctl", "daemon-reload")
    run("systemctl", "start", f"{UNIT}.socket")
    run("systemctl", "try-restart", f"{UNIT}.service")


def read_or_create(name, size):
    path = STATE / name
    if not path.exists():
        write(path, secrets.token_hex(size) + "\n", 0o600)
    return path.read_text(encoding="utf-8").strip()


def install():
    prior_path = STATE / "install.json"
    prior = json.loads(prior_path.read_text()) if prior_path.exists() else {}
    domain, path = address(ask("Public hostname", prior.get("domain", "")),
                           ask("Status URL path", "/status"))
    mode, container, config = choose_proxy(prior)
    proxy = Proxy(mode, container, config)
    if prior and (mode, container, str(proxy.config), proxy.listener) != (
            prior["mode"], prior["container"], prior["config"], prior["listener"]):
        raise ValueError("This installation is attached to another proxy or socket. Restore that setup before updating it.")

    try:
        socket.getaddrinfo(domain, 443, type=socket.SOCK_STREAM)
    except socket.gaierror as error:
        raise ValueError(f"DNS for {domain} did not resolve. Add its public A/AAAA records and try again.") from error

    instance = read_or_create("instance", 16)
    token = read_or_create("token", 32)
    endpoint = f"https://{domain}{path}"
    trusted = has_valid_tls(domain)
    if trusted:
        check_existing_path(endpoint, instance)
        print(f"Reusing the existing trusted TLS endpoint for {domain}.")
    else:
        print(f"No trusted HTTPS certificate served for {domain}. Caddy must complete ACME issuance.")

    original = proxy.config.read_bytes().decode("utf-8")
    before = proxy.adapt(original)
    proposed = plan(original, domain, path, proxy.upstream, proxy.environment, trusted)
    proxy.validate(proposed)
    restart = before.get("admin", {}).get("disabled", False)
    changed = proposed != original
    if changed and restart:
        print("Caddy's admin API is disabled. Applying the route needs one brief Caddy restart.")
        if ask("Restart Caddy after saving the validated configuration? (yes/no)", "no").lower() != "yes":
            raise ValueError("Caddy restart declined. Its configuration is unchanged.")

    applied = False
    try:
        install_service(proxy.listener)
        if changed:
            backup = STATE / "Caddyfile.previous"
            write(backup, original, 0o600)
            proxy.replace(original, proposed)
            applied = True
            proxy.apply(restart)
        print(f"Verifying HTTPS at {endpoint}…", flush=True)
        verify(endpoint, instance, token)
        run("systemctl", "enable", f"{UNIT}.socket")
        record = dict(domain=domain, path=path, mode=mode, container=container, source=proxy.source,
                      config=str(proxy.config), listener=proxy.listener)
        write(prior_path, json.dumps(record, indent=2) + "\n", 0o600)
    except BaseException:
        if applied:
            try:
                proxy.replace(proposed, original)
                proxy.apply(restart)
                print("Restored the previous Caddyfile.", file=sys.stderr)
            except (OSError, RuntimeError, subprocess.SubprocessError) as error:
                print(f"Automatic Caddy rollback failed. Backup at {STATE / 'Caddyfile.previous'}. {error}",
                      file=sys.stderr)
        if not prior:
            try:
                run("systemctl", "disable", "--now", f"{UNIT}.socket")
                run("systemctl", "stop", f"{UNIT}.service")
            except (RuntimeError, subprocess.SubprocessError):
                print(f"Check {UNIT}.socket and {UNIT}.service with systemctl.", file=sys.stderr)
        raise

    print(f"Verified endpoint: {endpoint}")
    print("In Connect IQ settings, enter the HTTPS endpoint and the read-only status token.")
    command = "sudo sh " + shlex.quote(str(Path(__file__).resolve().parent.parent / "install.sh"))
    print(f"Show token:   {command} show-token")
    print(f"Rotate token: {command} rotate-token")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", nargs="?", default="install", choices=["install", "show-token", "rotate-token"])
    options = parser.parse_args()
    if not sys.platform.startswith("linux") or os.geteuid() != 0:
        raise SystemExit("Run this command as root on the Linux host, using sudo")
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (STATE / "lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if options.action == "install":
            install()
            return
        if not (STATE / "install.json").is_file():
            raise ValueError("Install and verify Server Status before managing the token")
        token_file = STATE / "token"
        if options.action == "show-token":
            print(token_file.read_text().strip())
            return
        old_token = token_file.read_text()
        token = secrets.token_hex(32)
        write(token_file, token + "\n", 0o600)
        try:
            run("systemctl", "try-restart", f"{UNIT}.service")
        except (RuntimeError, subprocess.SubprocessError):
            write(token_file, old_token, 0o600)
            run("systemctl", "try-restart", f"{UNIT}.service")
            raise
        print("The previous token is revoked. Enter this token in Connect IQ settings.")
        print(token)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, EOFError) as error:
        raise SystemExit(f"Server Status setup failed. {error}")
    except KeyboardInterrupt:
        raise SystemExit("Server Status setup cancelled")
