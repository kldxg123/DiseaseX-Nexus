from __future__ import annotations

import http.client
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


WIDTH = int(os.environ.get("FRAME_WIDTH", "390"))
HEIGHT = int(os.environ.get("FRAME_HEIGHT", "844"))
PORT = int(os.environ.get("FRAME_PORT", "8141"))
UPSTREAM_PORT = int(os.environ.get("UPSTREAM_PORT", "8130"))
FRAME_PATH = os.environ.get("FRAME_PATH", "/")
FRAME = f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>P1-10R viewport check</title><style>html,body{{margin:0;padding:0;background:#dbe5ec}}iframe{{display:block;width:{WIDTH}px;height:{HEIGHT}px;border:0;background:#fff}}</style></head><body><iframe title='P1-10R {WIDTH}x{HEIGHT}' src='{FRAME_PATH}'></iframe></body></html>""".encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _proxy(self) -> None:
        if self.path == "/mobile_frame.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(FRAME)))
            self.end_headers()
            self.wfile.write(FRAME)
            return
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else None
        forwarded = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in {"host", "content-length", "connection", "accept-encoding"}
        }
        connection = http.client.HTTPConnection("127.0.0.1", UPSTREAM_PORT, timeout=15)
        try:
            connection.request(self.command, self.path, body=body, headers=forwarded)
            response = connection.getresponse()
            payload = response.read()
            self.send_response(response.status)
            for key, value in response.getheaders():
                lower = key.lower()
                if lower in {
                    "connection",
                    "content-length",
                    "transfer-encoding",
                    "x-frame-options",
                    "content-security-policy",
                }:
                    continue
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        finally:
            connection.close()

    do_GET = _proxy
    do_POST = _proxy


ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
