from __future__ import annotations

import os
import select
import signal
import socketserver
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import a100_p1_8ab as a100

ROOT = Path(__file__).resolve().parent.parent
PID_FILE = ROOT / "p1_9_tunnel.pid"


class ReusableThreadingTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True


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


def main() -> None:
    connection, _gate = a100.connect()
    transport = connection.get_transport()
    assert transport is not None
    transport.set_keepalive(30)
    servers: list[ReusableThreadingTCPServer] = []
    threads: list[threading.Thread] = []
    for local_port, remote_port in ((8122, 18020), (8123, 18021)):
        handler = handler_for(remote_port)
        handler.transport = transport
        server = ReusableThreadingTCPServer(("127.0.0.1", local_port), handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append(server)
        threads.append(thread)
    PID_FILE.write_text(str(os.getpid()), encoding="ascii")
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
        PID_FILE.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
