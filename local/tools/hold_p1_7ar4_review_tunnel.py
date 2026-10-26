from __future__ import annotations

import os
import select
import signal
import socketserver
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import autodl_p1_7ar4 as p1r4


class ReusableThreadingTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True


class ReviewForwardHandler(socketserver.BaseRequestHandler):
    transport = None

    def handle(self) -> None:
        assert self.transport is not None
        channel = self.transport.open_channel(
            "direct-tcpip", ("127.0.0.1", 8021), self.request.getpeername()
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


def main() -> None:
    connection = p1r4._connect()
    transport = connection.get_transport()
    assert transport is not None
    ReviewForwardHandler.transport = transport
    server = ReusableThreadingTCPServer(("127.0.0.1", 18021), ReviewForwardHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    pid_file = Path(__file__).resolve().parent / "p1_7ar4_review_tunnel.pid"
    pid_file.write_text(str(os.getpid()), encoding="ascii")
    if "--daemon" not in sys.argv:
        print("P1_7AR4_REVIEW_TUNNEL_READY", flush=True)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_args: stop.set())
    signal.signal(signal.SIGINT, lambda *_args: stop.set())
    try:
        if "--daemon" in sys.argv:
            stop.wait()
        else:
            sys.stdin.readline()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        connection.close()
        pid_file.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
