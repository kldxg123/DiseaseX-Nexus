from __future__ import annotations
import concurrent.futures as cf
import hashlib, json, re, urllib.request, urllib.error
from pathlib import Path
from datetime import datetime, timezone

REPORT=Path("/tmp/us_legionellosis_report_dir.txt").read_text().strip()
OUTDIR=Path(REPORT)/"artifacts/cdc_stacks_id_scan"
OUTDIR.mkdir(parents=True, exist_ok=True)

def fetch(cid:int):
    url=f"https://stacks.cdc.gov/view/cdc/{cid}/cdc_{cid}_DS2.txt"
    try:
        req=urllib.request.Request(url,headers={"User-Agent":"epidemic-openclaw-stacks/1.0"})
        with urllib.request.urlopen(req,timeout=8) as resp:
            data=resp.read(80000)
        text=data.decode("utf-8","replace")
        if "Legionellosis" not in text or "Weekly cases" not in text:
            return None
        m=re.search(r"week ending ([^(]+) \\(Week (\\d+)\\)", text)
        title=text.splitlines()[1] if len(text.splitlines())>1 else text[:120]
        path=OUTDIR/f"cdc_{cid}_DS2.txt"
        path.write_bytes(data)
        return {"cdc_id":cid,"url":url,"bytes":len(data),"sha256":hashlib.sha256(data).hexdigest(),"week_ending_text":m.group(1).strip() if m else None,"mmwr_week":int(m.group(2)) if m else None,"title":title}
    except Exception:
        return None

def main():
    # Nearby ID range around known 2026 week 28 id 258228. Bounded to keep load small.
    ids=list(range(258500,254500,-1))
    found=[]
    with cf.ThreadPoolExecutor(max_workers=12) as ex:
        for item in ex.map(fetch, ids):
            if item:
                found.append(item)
                if len(found)>=30:
                    break
    found=sorted(found,key=lambda x:x["cdc_id"], reverse=True)
    payload={"generated_at_utc":datetime.now(timezone.utc).replace(microsecond=0).isoformat(),"range":[min(ids),max(ids)],"item_count":len(found),"items":found}
    out=Path(REPORT)/"US_LEGIONELLOSIS_STACKS_ID_SCAN.json"
    out.write_text(json.dumps(payload,indent=2,sort_keys=True),encoding="utf-8")
    print(json.dumps({"item_count":len(found),"ids":[x["cdc_id"] for x in found[:10]],"out":str(out)},sort_keys=True))

if __name__=="__main__":
    main()
