from __future__ import annotations
import hashlib, json, time, urllib.request, urllib.error
from pathlib import Path
from datetime import datetime, timezone

REPORT=Path("/tmp/us_legionellosis_report_dir.txt").read_text().strip()
OUT=Path(REPORT)/"CDC_STACKS_COLLECTION_EXPORT_ROWS_LIMIT_PROBE.json"
ART=Path(REPORT)/"artifacts/cdc_stacks_rows_limit"
ART.mkdir(parents=True,exist_ok=True)
rows_values=[1,2,5,10,20,25,50,100,200]
results=[]
for rows in rows_values:
    url=f"https://stacks.cdc.gov/fedora/export/download/collection/cdc%3A49375?rows={rows}&start=0"
    fetched=datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    try:
        req=urllib.request.Request(url,headers={"User-Agent":"epidemic-openclaw-row-limit/1.0"})
        with urllib.request.urlopen(req,timeout=45) as resp:
            body=resp.read(2_000_000)
            status=resp.status
            headers=dict(resp.headers.items())
    except urllib.error.HTTPError as e:
        body=e.read(500000); status=e.code; headers=dict(e.headers.items())
    digest=hashlib.sha256(body).hexdigest()
    path=ART/f"rows_{rows}_{status}_{digest[:12]}.bin"
    path.write_bytes(body)
    results.append({"rows":rows,"status":status,"fetched_at_utc":fetched,"body_size_bytes":len(body),"body_sha256":digest,"content_type":headers.get("Content-Type"),"raw_file":str(path.relative_to(Path(REPORT))),"body_prefix":body.decode("utf-8","replace")[:300]})
    time.sleep(1)
OUT.write_text(json.dumps({"results":results},indent=2,sort_keys=True),encoding="utf-8")
print(json.dumps([(r["rows"],r["status"],r["body_size_bytes"]) for r in results]))
