from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

REPORT = Path("/tmp/us_legionellosis_report_dir.txt").read_text().strip()
OUT = Path(REPORT) / "CDC_NNDSS_V3_POST_PROBE.json"
payload = {
    "query": "SELECT `states`, `year`, `week`, `label`, `m1`, `m1_flag`, `m2`, `m2_flag`, `m3`, `m3_flag`, `m4`, `m4_flag`, `location1`, `location2`, `sort_order` WHERE `label`='Legionellosis'",
    "page": {"pageNumber": 1, "pageSize": 5},
    "includeSynthetic": False,
}
req = urllib.request.Request(
    "https://data.cdc.gov/api/v3/views/x9gk-5huc/query.json",
    data=json.dumps(payload).encode(),
    method="POST",
    headers={
        "User-Agent": "epidemic-openclaw/1.0",
        "Accept": "application/json",
        "Content-Type": "application/json",
    },
)
try:
    with urllib.request.urlopen(req, timeout=45) as resp:
        body = resp.read(200000)
        result = {
            "status": resp.status,
            "content_type": resp.headers.get("content-type"),
            "body_prefix": body.decode("utf-8", "replace")[:1000],
        }
except urllib.error.HTTPError as e:
    result = {"status": e.code, "error_type": "HTTPError", "body_prefix": e.read(1000).decode("utf-8", "replace")}
except Exception as e:
    result = {"status": "ERROR", "error_type": type(e).__name__, "message": str(e)[:500]}
OUT.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
print(json.dumps(result, sort_keys=True))
