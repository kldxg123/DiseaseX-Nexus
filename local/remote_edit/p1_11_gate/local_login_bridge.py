from __future__ import annotations

import html
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


credential_file = Path(sys.argv[1]).resolve()
target = sys.argv[2]
port = int(sys.argv[3])
lines = credential_file.read_text(encoding="utf-8").splitlines()
if len(lines) < 2 or not lines[0] or not lines[1]:
    raise SystemExit("INVALID_LOCAL_LOGIN_FILE")
username, password = lines[0], lines[1]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        body = (
            "<!doctype html><meta charset='utf-8'><title>Local login bridge</title>"
            f"<form id='f' method='post' action='{html.escape(target, quote=True)}'>"
            f"<input type='hidden' name='username' value='{html.escape(username, quote=True)}'>"
            f"<input type='hidden' name='password' value='{html.escape(password, quote=True)}'>"
            "</form><script>document.getElementById('f').submit()</script>"
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
try:
    server.serve_forever(poll_interval=0.1)
finally:
    server.server_close()
    username = ""
    password = ""
