from __future__ import annotations

import select
import signal
import socketserver
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import a100_p1_11 as p111  # noqa: E402


class ReusableThreadingTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class TunnelState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.connection = None
        self.transport = None

    def get_transport(self):
        with self.lock:
            if self.transport is not None and self.transport.is_active():
                return self.transport
            if self.connection is not None:
                try:
                    self.connection.close()
                except Exception:
                    pass
            self.connection, _ = p111.p110r3.previous.p19.a100.connect()
            self.transport = self.connection.get_transport()
            assert self.transport is not None
            self.transport.set_keepalive(20)
            return self.transport

    def close(self) -> None:
        with self.lock:
            if self.connection is not None:
                self.connection.close()
            self.connection = None
            self.transport = None


def handler_for(state: TunnelState, remote_port: int):
    class ForwardHandler(socketserver.BaseRequestHandler):
        def handle(self) -> None:
            transport = state.get_transport()
            channel = transport.open_channel(
                "direct-tcpip",
                ("127.0.0.1", remote_port),
                self.request.getpeername(),
            )
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
    local_dashboard = int(sys.argv[1]) if len(sys.argv) > 1 else 8124
    local_review = int(sys.argv[2]) if len(sys.argv) > 2 else 8125
    state = TunnelState()
    state.get_transport()
    servers: list[ReusableThreadingTCPServer] = []
    threads: list[threading.Thread] = []
    for local_port, remote_port in ((local_dashboard, 8020), (local_review, 8021)):
        handler = handler_for(state, remote_port)
        server = ReusableThreadingTCPServer(("127.0.0.1", local_port), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append(server)
        threads.append(thread)
    print(
        f"A100_DASHBOARD_TUNNELS_READY local_dashboard=http://127.0.0.1:{local_dashboard} local_review=http://127.0.0.1:{local_review}",
        flush=True,
    )
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_args: stop.set())
    signal.signal(signal.SIGINT, lambda *_args: stop.set())
    try:
        while not stop.wait(10):
            transport = state.get_transport()
            if not transport.is_active():
                time.sleep(1)
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=5)
        state.close()


if __name__ == "__main__":
    main()
