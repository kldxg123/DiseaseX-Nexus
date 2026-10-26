from __future__ import annotations

import argparse
import html
import http.cookiejar
import re
import urllib.parse
import urllib.request
from pathlib import Path


def credentials(path: Path) -> tuple[str, str]:
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    values: dict[str, str] = {}
    for line in lines:
        if "=" in line:
            key, value = line.split("=", 1)
            values[key.strip().lower()] = value.strip()
    username = values.get("username") or values.get("user") or re.sub(r"^(username|user)\s*[:=]\s*", "", lines[0], flags=re.I)
    password = values.get("password") or values.get("pass")
    if password is None and len(lines) > 1:
        password = re.sub(r"^(password|pass)\s*[:=]\s*", "", lines[1], flags=re.I)
    if not username or not password:
        raise RuntimeError("review credential format is invalid")
    return username, password


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--credential", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    username, password = credentials(args.credential)
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    request = urllib.request.Request(
        "http://127.0.0.1:8021/login",
        data=urllib.parse.urlencode({"username": username, "password": password}).encode(),
        method="POST",
    )
    opener.open(request, timeout=10).read()
    index = opener.open("http://127.0.0.1:8021/reviews", timeout=10).read().decode("utf-8")
    match = re.search(r'href="(/reviews/[0-9a-f-]+)"', index)
    if not match:
        raise RuntimeError("review case link is missing")
    detail = opener.open("http://127.0.0.1:8021" + match.group(1), timeout=10).read().decode("utf-8")
    detail = detail.replace('href="/static/', 'href="http://127.0.0.1:8021/static/')
    detail = detail.replace('src="/static/', 'src="http://127.0.0.1:8021/static/')
    source = html.escape(detail, quote=True)
    harness = f"""<!doctype html><html><head><meta charset="utf-8"><title>P1-7A-R3 viewport harness</title>
<style>body{{margin:0;background:#334155;color:white;font-family:system-ui}}h1,h2{{margin:12px}}iframe{{display:block;border:2px solid #0ea5e9;background:white;margin:12px}}</style></head><body>
<h1>Authenticated review render harness</h1><h2>Desktop 1440 × 900</h2>
<iframe id="desktop" title="Desktop review" sandbox="allow-same-origin" width="1440" height="900" srcdoc="{source}"></iframe>
<h2>Mobile 390 × 844</h2>
<iframe id="mobile" title="Mobile review" sandbox="allow-same-origin" width="390" height="844" srcdoc="{source}"></iframe>
</body></html>"""
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(harness, encoding="utf-8")


if __name__ == "__main__":
    main()
