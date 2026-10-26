from __future__ import annotations

import csv
import hashlib
import json
import os
import ssl
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


URL = "https://xmart-api-public.who.int/FLUMART/VIW_FID_EPI?%24format=csv"
ROOT = Path("USER_HOME/projects/openclaw-fresh-ukhsa-20260814/runtime/fresh_source")
RAW = ROOT / "who_fluid_frozen_20260814.csv"
META = ROOT / "who_fluid_frozen_20260814.metadata.json"
MAX_BYTES = 256 * 1024 * 1024


def main() -> None:
    if RAW.exists() or META.exists():
        raise SystemExit("OUTPUT_ALREADY_EXISTS")
    ROOT.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(URL, headers={"User-Agent": "epidemic-openclaw-official-source-freezer/1.0"})
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=ssl.create_default_context()),
    )
    digest = hashlib.sha256()
    total = 0
    fetched_at = datetime.now(timezone.utc).isoformat()
    fd, temp_name = tempfile.mkstemp(prefix="who_fluid_", suffix=".part", dir=ROOT)
    os.close(fd)
    temp_path = Path(temp_name)
    headers: dict[str, str] = {}
    status = None
    final_url = None
    try:
        with opener.open(request, timeout=120) as response, temp_path.open("wb") as handle:
            status = response.status
            final_url = response.geturl()
            headers = {
                key: value
                for key, value in response.headers.items()
                if key.lower() in {"content-type", "content-length", "etag", "last-modified", "content-disposition"}
            }
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_BYTES:
                    raise ValueError("SOURCE_EXCEEDS_MAX_BYTES")
                digest.update(chunk)
                handle.write(chunk)
        if status != 200:
            raise ValueError(f"UNEXPECTED_HTTP_STATUS:{status}")
        with temp_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle)
            fieldnames = next(reader)
            row_count = sum(1 for _ in reader)
        os.replace(temp_path, RAW)
        metadata = {
            "source_url": URL,
            "final_url": final_url,
            "publisher": "World Health Organization",
            "dataset": "fluID",
            "fetched_at": fetched_at,
            "http_status": status,
            "response_headers": headers,
            "bytes": total,
            "sha256": digest.hexdigest(),
            "row_count": row_count,
            "fieldnames": fieldnames,
            "tls_verified": True,
            "proxy_used": False,
            "values_emitted": False,
        }
        META.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(metadata, ensure_ascii=False, sort_keys=True))
    finally:
        if temp_path.exists():
            temp_path.unlink()


if __name__ == "__main__":
    main()
