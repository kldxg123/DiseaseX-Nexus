from __future__ import annotations
import urllib.request
from pathlib import Path
REPORT=Path("/tmp/us_legionellosis_report_dir.txt").read_text().strip()
url="https://stacks.cdc.gov/gsearch?ref=docDetails&subject_topic=Legionellosis"
req=urllib.request.Request(url,headers={"User-Agent":"epidemic-openclaw-stacks/1.0"})
with urllib.request.urlopen(req,timeout=45) as resp:
    data=resp.read(2_000_000)
out=Path(REPORT)/"artifacts/gsearch_legionellosis.html"
out.write_bytes(data)
print(resp.status, len(data), out)
