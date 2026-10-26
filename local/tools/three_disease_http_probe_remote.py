from __future__ import annotations

import json
import time
import urllib.error
import urllib.request


def get(path: str) -> dict[str, object]:
    url = "http://127.0.0.1:8020" + path
    start = time.time()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "three-disease-repair-probe/1.0"}), timeout=15) as r:
            data = r.read()
            return {
                "path": path,
                "status": getattr(r, "status", 200),
                "content_type": r.headers.get("Content-Type"),
                "elapsed_ms": int((time.time() - start) * 1000),
                "bytes": len(data),
                "body": data.decode("utf-8", errors="replace"),
            }
    except urllib.error.HTTPError as exc:
        data = exc.read()
        return {
            "path": path,
            "status": exc.code,
            "content_type": exc.headers.get("Content-Type") if exc.headers else None,
            "elapsed_ms": int((time.time() - start) * 1000),
            "bytes": len(data),
            "body": data.decode("utf-8", errors="replace"),
        }
    except Exception as exc:  # noqa: BLE001
        return {"path": path, "status": 0, "error_type": type(exc).__name__, "error": str(exc)}


def main() -> None:
    paths = [
        "/healthz",
        "/readyz",
        "/api/v2/catalog/options",
        "/api/v2/history/series",
        "/api/v2/history/metadata",
        "/",
        "/fasttrack-forecast",
    ]
    print(json.dumps({"items": [get(path) for path in paths]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
