"""Safely probe an SSH host key without authenticating.

This helper intentionally reads only the first non-empty line of the explicitly
provided server information file. It never reads an authentication secret and
never adds a key to a known-hosts store.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import socket
from pathlib import Path

import paramiko


SSH_LINE = re.compile(
    r"^ssh\s+-p\s+(?P<port>\d{1,5})\s+(?P<user>[^@\s]+)@(?P<host>[^\s]+)$",
    re.IGNORECASE,
)


def parse_target(path: Path) -> tuple[str, int, bool]:
    if not path.is_file():
        raise ValueError("server information path is not a regular file")
    first_line = ""
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            if line.strip():
                first_line = line.strip()
                break
    match = SSH_LINE.fullmatch(first_line)
    if not match:
        raise ValueError("first non-empty line is not a supported SSH command")
    port = int(match.group("port"))
    if not 1 <= port <= 65535:
        raise ValueError("SSH port is outside 1..65535")
    return match.group("host"), port, match.group("user") == "root"


def probe(host: str, port: int, timeout: float) -> tuple[str, str]:
    sock: socket.socket | None = None
    transport: paramiko.Transport | None = None
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
        transport = paramiko.Transport(sock)
        transport.start_client(timeout=timeout)
        key = transport.get_remote_server_key()
        digest = hashlib.sha256(key.asbytes()).digest()
        fingerprint = "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")
        return key.get_name(), fingerprint
    finally:
        if transport is not None:
            transport.close()
        if sock is not None:
            sock.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--server-info", required=True, type=Path)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    try:
        host, port, is_root = parse_target(args.server_info)
    except Exception as exc:
        print(json.dumps({"status": "PARSE_FAILED", "error_type": type(exc).__name__}))
        return 2

    base = {
        "authentication_attempted": False,
        "host_key_trust": "UNVERIFIED",
        "username_policy": "BLOCKED_PROHIBITED_ACCOUNT" if is_root else "NON_ROOT",
    }
    if args.dry_run:
        print(json.dumps({**base, "status": "WOULD_PROBE_HOST_KEY"}))
        return 0

    try:
        key_type, fingerprint = probe(host, port, args.timeout)
    except Exception as exc:
        print(
            json.dumps(
                {**base, "status": "HOST_KEY_PROBE_FAILED", "error_type": type(exc).__name__}
            )
        )
        return 1
    print(
        json.dumps(
            {
                **base,
                "key_type": key_type,
                "sha256": fingerprint,
                "status": "PENDING_USER_CONFIRMATION",
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
