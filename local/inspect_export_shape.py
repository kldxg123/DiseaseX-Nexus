from __future__ import annotations
import json
from pathlib import Path
REPORT=Path("/tmp/us_legionellosis_report_dir.txt").read_text().strip()
probe=json.loads((Path(REPORT)/"CDC_STACKS_COLLECTION_EXPORT_PROBE.json").read_text(encoding="utf-8"))
for r in probe["requests"]:
    if "collection/cdc%3A49375" in r["request_url"] and "/csv/" not in r["request_url"] and "/xmlFormat/" not in r["request_url"]:
        data=json.loads((Path(REPORT)/r["raw_file"]).read_text(encoding="utf-8",errors="replace"))
        print("top", type(data), data.keys() if isinstance(data,dict) else None)
        print(json.dumps(data,ensure_ascii=False,indent=2)[:8000])
