"""Publish only an aggregate of unsuccessful OpenSSH connections."""

import json
import os
from pathlib import Path
import re
import subprocess
import time

SNAPSHOT = Path("/run/garmin-server-status-ssh/count.json")
WINDOW = 24 * 60 * 60
FAILURE = re.compile(
    r"^(?:Failed \S+ for |Invalid user |Authentication refused:|"
    r"Refusing certificate\b|Certificate invalid:|Unable to negotiate\b|"
    r"maximum authentication attempts exceeded\b|Timeout before authentication\b|"
    r"kex_exchange_identification: (?:Connection closed|read: Connection reset)|"
    r"(?:Connection (?:closed|reset) by|Disconnected from|Received disconnect from) .+ \[preauth\]$)",
    re.IGNORECASE,
)
PEER = re.compile(r"(?:^|\s)([0-9a-fA-F:.]+) port (\d+)(?=\s|:|$)")


def count_connections(entries):
    sessions = {}
    peers = {}
    for entry in entries:
        if entry.get("SYSLOG_IDENTIFIER") not in {"sshd", "sshd-session", "sshd-auth"}:
            continue
        message = entry.get("MESSAGE")
        boot, pid = entry.get("_BOOT_ID"), entry.get("_PID")
        if not isinstance(message, str) or not boot or not pid:
            continue
        message = re.sub(r"^(?:error|fatal): ", "", message)
        accepted = message.startswith("Accepted ")
        if not accepted and not FAILURE.match(message):
            continue
        session = boot, pid
        match = PEER.search(message)
        if match:
            peers[session] = (boot, *match.groups())
        sessions[session] = accepted or sessions.get(session, False)
    connections = {}
    for session, accepted in sessions.items():
        key = peers.get(session, session)
        connections[key] = accepted or connections.get(key, False)
    return sum(not accepted for accepted in connections.values())


def main():
    now = int(time.time())
    journal = subprocess.run(
        ["/usr/bin/journalctl", "--system", "--quiet", "--no-pager", "--output=json",
         "--output-fields=MESSAGE,SYSLOG_IDENTIFIER,_BOOT_ID,_PID",
         f"--since=@{now - WINDOW}", f"--until=@{now}", "-u", "ssh.service", "-u", "sshd.service",
         "-u", "ssh@*.service", "-u", "sshd@*.service"],
        check=True, capture_output=True, text=True, timeout=40,
    )
    count = count_connections(json.loads(line) for line in journal.stdout.splitlines())
    temp = SNAPSHOT.with_suffix(".tmp")
    temp.write_text(json.dumps({"count": count, "sampled_at": now}), encoding="ascii")
    temp.chmod(0o644)
    os.replace(temp, SNAPSHOT)


if __name__ == "__main__":
    main()
