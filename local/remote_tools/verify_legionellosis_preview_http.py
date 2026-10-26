from __future__ import annotations

import json
import urllib.error
import urllib.request


def probe(path: str) -> dict[str, object]:
    url = "http://127.0.0.1:8020" + path
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = resp.read(2048)
            return {"path": path, "status": resp.status, "location": resp.headers.get("Location"), "sample": body[:80].decode(errors="replace")}
    except urllib.error.HTTPError as exc:
        body = exc.read(2048)
        return {"path": path, "status": exc.code, "location": exc.headers.get("Location"), "sample": body[:80].decode(errors="replace")}


def main() -> None:
    results = [probe("/healthz"), probe("/api/v1/legionellosis-preview"), probe("/legionellosis-preview")]
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
