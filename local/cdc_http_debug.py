from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

REPORT = Path("/tmp/us_legionellosis_report_dir.txt").read_text().strip()
OUT = Path(REPORT) / "CDC_NNDSS_HTTP_DEBUG.json"
URLS = [
    "https://data.cdc.gov/api/views/x9gk-5huc",
    "https://data.cdc.gov/resource/x9gk-5huc.json?$limit=1",
    "https://data.cdc.gov/resource/x9gk-5huc.json?$select=count(*)",
]

results = {}
for url in URLS:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "epidemic-openclaw/1.0"})
        with urllib.request.urlopen(req, timeout=45) as resp:
            data = resp.read(2000)
            results[url] = {
                "status": resp.status,
                "content_type": resp.headers.get("content-type"),
                "etag_present": bool(resp.headers.get("etag")),
                "last_modified_present": bool(resp.headers.get("last-modified")),
                "body_prefix": data.decode("utf-8", "replace")[:500],
            }
    except urllib.error.HTTPError as e:
        results[url] = {"status": e.code, "error_type": "HTTPError", "body_prefix": e.read(500).decode("utf-8", "replace")}
    except Exception as e:
        results[url] = {"status": "ERROR", "error_type": type(e).__name__, "message": str(e)[:500]}

OUT.write_text(json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")
print(json.dumps(results, sort_keys=True))
