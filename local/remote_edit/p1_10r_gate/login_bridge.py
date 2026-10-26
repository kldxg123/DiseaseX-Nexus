from __future__ import annotations

import html
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import a100_p1_9 as p19  # noqa: E402


def credentials() -> tuple[str, str]:
    client, _gate = p19.a100.connect()
    try:
        _root, project = p19.a100.paths(client)
        sftp = client.open_sftp()
        try:
            with sftp.file(project + "/storage/p1_9_demo/secrets/login_runtime.json", "rb") as handle:
                value = json.loads(handle.read().decode("utf-8"))
        finally:
            sftp.close()
    finally:
        client.close()
    return str(value["username"]), str(value["password"])


USERNAME, PASSWORD = credentials()
TARGET = os.environ.get("P1_10R_LOGIN_TARGET", "http://127.0.0.1:8130/login")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        body = (
            "<!doctype html><meta charset='utf-8'><title>Local test login</title>"
            f"<form id='f' method='post' action='{html.escape(TARGET, quote=True)}'>"
            f"<input type='hidden' name='username' value='{html.escape(USERNAME, quote=True)}'>"
            f"<input type='hidden' name='password' value='{html.escape(PASSWORD, quote=True)}'>"
            "</form><script>document.getElementById('f').submit()</script>"
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        threading.Thread(target=self.server.shutdown, daemon=True).start()


server = ThreadingHTTPServer(("127.0.0.1", 8139), Handler)
try:
    server.serve_forever()
finally:
    server.server_close()
    USERNAME = ""
    PASSWORD = ""
