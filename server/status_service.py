#!/usr/bin/env python3
"""Serve a small authenticated Linux host snapshot over a systemd socket."""

import hmac
import json
import os
import socket
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Event, Thread

PROC = Path("/proc")
SAMPLE_INTERVAL = 2
SSH_SNAPSHOT = Path("/run/garmin-server-status-ssh/count.json")


def cpu_counters(proc):
    with (proc / "stat").open() as source:
        fields = source.readline().split()
    if fields[0] != "cpu" or len(fields) < 9:
        raise ValueError("Missing aggregate CPU counters")
    counters = list(map(int, fields[1:9]))
    return sum(counters), counters[3] + counters[4]


def percent(used, total):
    return max(0, min(100, round(100 * used / total))) if total > 0 else 0


def read_metrics(proc, disk, previous, current):
    with (proc / "meminfo").open() as source:
        memory = {line.split(":")[0]: int(line.split()[1]) for line in source
                  if line.startswith(("MemTotal:", "MemAvailable:"))}
    fs = os.statvfs(disk)
    used = fs.f_blocks - fs.f_bfree
    return {
        "version": 1,
        "cpu": percent((current[0] - previous[0]) - (current[1] - previous[1]), current[0] - previous[0]),
        "ram": percent(memory["MemTotal"] - memory["MemAvailable"], memory["MemTotal"]),
        "disk": percent(used, used + fs.f_bavail),
        "load": [round(float(value) * 100) for value in (proc / "loadavg").read_text().split()[:3]],
        "uptime_s": int(float((proc / "uptime").read_text().split()[0])),
        "sampled_at": int(time.time()),
    }


def read_ssh_count(path=SSH_SNAPSHOT):
    try:
        snapshot = json.loads(path.read_text(encoding="ascii"))
        count, sampled_at = snapshot["count"], snapshot["sampled_at"]
        if type(count) is int and count >= 0 and 0 <= time.time() - sampled_at <= 180:
            return count
    except (OSError, ValueError, TypeError, KeyError):
        pass
    return None


class Sampler:
    def __init__(self, proc=PROC, disk="/"):
        self.proc = proc
        self.disk = disk
        self.sample = None
        self.stop = Event()

    def run(self):
        previous = None
        while not self.stop.is_set():
            try:
                current = cpu_counters(self.proc)
                if previous is not None:
                    self.sample = (time.monotonic(), read_metrics(self.proc, self.disk, previous, current))
                previous = current
            except (OSError, ValueError, IndexError, KeyError) as error:
                self.sample = None
                previous = None
                print(f"Could not sample Linux metrics: {error}", flush=True)
            self.stop.wait(SAMPLE_INTERVAL)

    def reading(self):
        sample = self.sample
        if not sample or time.monotonic() - sample[0] > 10:
            return None
        return dict(sample[1], ssh_failed_24h=read_ssh_count())


class StatusHandler(BaseHTTPRequestHandler):
    def setup(self):
        self.request.settimeout(3)
        super().setup()

    def log_message(self, *_):
        pass

    def reply(self, status, data):
        body = json.dumps(data, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Garmin-Status", self.server.instance)
        if status == 401:
            self.send_header("WWW-Authenticate", 'Bearer realm="Server Status"')
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self):
        if self.path != "/metrics":
            self.reply(404, {"error": "not_found"})
            return
        headers = self.headers.get_all("Authorization", [])
        if len(headers) != 1 or not hmac.compare_digest(headers[0].encode("latin-1"), self.server.authorization):
            self.reply(401, {"error": "unauthorized"})
            return
        data = self.server.sampler.reading()
        if data is None:
            self.reply(503, {"error": "sample_unavailable"})
        else:
            self.reply(200, data)

    def reject_method(self):
        self.reply(405, {"error": "method_not_allowed"})

    do_HEAD = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = reject_method


class ActivatedServer(HTTPServer):
    def __init__(self, listener, sampler, token, instance):
        super().__init__(("localhost", 0), StatusHandler, bind_and_activate=False)
        self.socket.close()
        self.socket = listener
        self.server_name = "Server Status"
        self.server_port = 0
        self.sampler = sampler
        self.authorization = b"Bearer " + token
        self.instance = instance

    def get_request(self):
        request, address = super().get_request()
        return request, address if isinstance(address, tuple) else ("local", 0)


def main():
    if os.environ.get("LISTEN_PID") != str(os.getpid()) or os.environ.get("LISTEN_FDS") != "1":
        raise SystemExit("Start this service through garmin-server-status.socket")
    credentials = Path(os.environ["CREDENTIALS_DIRECTORY"])
    token = (credentials / "token").read_bytes().strip()
    instance = (credentials / "instance").read_text().strip()
    if len(token) != 64 or any(byte not in b"0123456789abcdef" for byte in token):
        raise SystemExit("Invalid Server Status token")
    sampler = Sampler()
    worker = Thread(target=sampler.run, daemon=True)
    worker.start()
    with ActivatedServer(socket.socket(fileno=3), sampler, token, instance) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
