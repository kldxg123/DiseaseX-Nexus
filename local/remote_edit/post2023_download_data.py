from __future__ import annotations

import hashlib
import json
import urllib.request
from pathlib import Path

url = (
    "https://data.gov.sg/api/action/datastore_search"
    "?resource_id=d_ac1eecf0886ff0bceefbc51556247015&limit=1000"
)
request = urllib.request.Request(
    url,
    headers={"User-Agent": "epidemic-openclaw-research-reproduction/1.0"},
)
with urllib.request.urlopen(request, timeout=60) as response:
    payload = response.read(2_000_000)
    status = response.status
    content_type = response.headers.get("Content-Type")
data = json.loads(payload)
assert data.get("success") is True
records = data["result"]["records"]
out = Path("/tmp/post2023_paper_benchmark/singapore_dengue_official.json")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_bytes(payload)
print(json.dumps({
    "status": status,
    "content_type": content_type,
    "bytes": len(payload),
    "sha256": hashlib.sha256(payload).hexdigest(),
    "record_count": len(records),
    "total": data["result"].get("total"),
}, sort_keys=True))
