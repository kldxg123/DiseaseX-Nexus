from __future__ import annotations

import argparse
import os
import select
import signal
import socketserver
import sys
import threading
from pathlib import Path

import paramiko

sys.path.insert(0, str(Path(__file__).parent))
import migrate_epidemic_to_a100 as migration


ROOT = Path(__file__).resolve().parent.parent
PRIVATE = ROOT / "private"
PID_FILE = PRIVATE / "a100_project_tunnel.pid"
KNOWN_HOSTS = PRIVATE / "known_hosts_a100_epidemic"


class ReusableThreadingTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True


def connect(known_hosts: Path) -> paramiko.SSHClient:
    credentials = migration.credentials(migration.TARGET_INFO)
    known_hosts.parent.mkdir(parents=True, exist_ok=True)
    known_hosts.touch(exist_ok=True)
    client = paramiko.SSHClient()
    client.load_host_keys(str(known_hosts))
    keys = paramiko.HostKeys(str(known_hosts))
    exact = migration.key_name(credentials)
    client.set_missing_host_key_policy(
        paramiko.RejectPolicy() if exact in keys else paramiko.AutoAddPolicy()
    )
    client.connect(
        credentials.host,
        credentials.port,
        credentials.user,
        credentials.password,
        look_for_keys=False,
        allow_agent=False,
        timeout=30,
        auth_timeout=30,
        banner_timeout=30,
    )
    client.save_host_keys(str(known_hosts))
    return client


def handler_for(remote_port: int):
    class ForwardHandler(socketserver.BaseRequestHandler):
        transport = None

        def handle(self) -> None:
            assert self.transport is not None
            channel = self.transport.open_channel(
                "direct-tcpip", ("127.0.0.1", remote_port), self.request.getpeername()
            )
            if channel is None:
                return
            try:
                while True:
                    ready, _, _ = select.select([self.request, channel], [], [], 5)
                    if self.request in ready:
                        data = self.request.recv(65536)
                        if not data:
                            break
                        channel.sendall(data)
                    if channel in ready:
                        data = channel.recv(65536)
                        if not data:
                            break
                        self.request.sendall(data)
            finally:
                channel.close()

    return ForwardHandler


def _mapping(value: str) -> tuple[int, int]:
    try:
        local_text, remote_text = value.split(":", 1)
        local_port, remote_port = int(local_text), int(remote_text)
    except (ValueError, TypeError) as exc:
        raise argparse.ArgumentTypeError("mapping must be LOCAL_PORT:REMOTE_PORT") from exc
    if not (1024 <= local_port <= 65535 and 1024 <= remote_port <= 65535):
        raise argparse.ArgumentTypeError("mapping ports must be between 1024 and 65535")
    return local_port, remote_port


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mapping", action="append", type=_mapping)
    parser.add_argument("--pid-file", type=Path)
    parser.add_argument("--known-hosts", type=Path)
    args = parser.parse_args()
    mappings = tuple(args.mapping or ((8120, 8020), (8121, 8021)))
    pid_file = args.pid_file.resolve() if args.pid_file else PID_FILE
    known_hosts = args.known_hosts.resolve() if args.known_hosts else KNOWN_HOSTS
    if ROOT not in pid_file.parents:
        raise RuntimeError("PID file must stay inside the project directory")
    if ROOT not in known_hosts.parents:
        raise RuntimeError("known_hosts must stay inside the project directory")
    connection = connect(known_hosts)
    transport = connection.get_transport()
    assert transport is not None
    transport.set_keepalive(30)
    servers = []
    threads = []
    for local_port, remote_port in mappings:
        handler = handler_for(remote_port)
        handler.transport = transport
        server = ReusableThreadingTCPServer(("127.0.0.1", local_port), handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append(server)
        threads.append(thread)
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(os.getpid()), encoding="ascii")
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_args: stop.set())
    signal.signal(signal.SIGINT, lambda *_args: stop.set())
    try:
        stop.wait()
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=5)
        connection.close()
        pid_file.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
