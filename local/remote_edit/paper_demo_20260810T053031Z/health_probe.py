#!/usr/bin/env python3
"""Read public loopback health endpoints and save raw response evidence."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent


def fetch(name: str) -> dict[str, object]:
    url = f"http://127.0.0.1:8020/{name}"
    with urlopen(Request(url, headers={"User-Agent": "epidemic-paper-readonly-audit/1"}), timeout=10) as response:
        body = response.read(2_000_000)
        headers = {k.lower(): v for k, v in response.headers.items()}
        final_url = response.geturl()
        status = response.status
    (ROOT / f"{name}.body").write_bytes(body)
    safe_headers = {
        k: v
        for k, v in headers.items()
        if k not in {"set-cookie", "authorization", "proxy-authorization"}
    }
    return {
        "name": name,
        "url": url,
        "final_url": final_url,
        "status": status,
        "content_type": headers.get("content-type"),
        "bytes": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
        "headers_redacted": safe_headers,
    }


def main() -> None:
    output = {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "requests": [fetch("healthz"), fetch("readyz")],
    }
    (ROOT / "health_probe.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
