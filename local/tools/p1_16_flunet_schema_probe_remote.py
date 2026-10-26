from __future__ import annotations
import csv, hashlib, io, json, os, urllib.request
from pathlib import Path
PROJECT=Path(os.environ['PROJECT'])
REPORT=PROJECT/'reports/ebola_historical_and_real_influenza_region_week_pipeline'
REPORT.mkdir(parents=True, exist_ok=True)
URLS={
 'flunet':'https://xmart-api-public.who.int/FLUMART/VIW_FNT?$format=csv',
 'metadata':'https://xmart-api-public.who.int/FLUMART/VIW_FLU_METADATA?$format=csv',
 'fluid':'https://xmart-api-public.who.int/FLUMART/VIW_FID_EPI?$format=csv',
}
res={}
for name,url in URLS.items():
    req=urllib.request.Request(url, headers={'User-Agent':'epidemic-openclaw-p1-16-schema-probe/1.0'})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data=r.read(2_000_000)
            final=r.geturl(); status=r.status; ctype=r.headers.get('content-type'); clen=r.headers.get('content-length')
        sha=hashlib.sha256(data).hexdigest()
        text=data.decode('utf-8-sig', errors='replace')
        sample=text.splitlines()[:8]
        reader=csv.reader(io.StringIO('\n'.join(sample)))
        rows=list(reader)
        header=rows[0] if rows else []
        res[name]={'url':url,'final_url':final,'status':status,'content_type':ctype,'content_length_header':clen,'sample_bytes':len(data),'sample_sha256':sha,'header':header,'sample_lines':sample[:3]}
    except Exception as e:
        res[name]={'url':url,'error':type(e).__name__+': '+str(e)}
(REPORT/'flunet_schema_probe.json').write_text(json.dumps(res,ensure_ascii=False,indent=2,sort_keys=True)+'\n',encoding='utf-8')
print(json.dumps(res,ensure_ascii=False,sort_keys=True))
