from __future__ import annotations
import os, json, hashlib, datetime as dt
from pathlib import Path
PROJECT=Path(os.environ['PROJECT']); REPORT=PROJECT/'reports/ebola_historical_and_real_influenza_region_week_pipeline'; APP=PROJECT/'src/epidemic_core/dashboard/app.py'
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
before=sha(APP)
lines=APP.read_text(encoding='utf-8').splitlines()
out=[]; i=0
while i < len(lines):
    if lines[i].lstrip().startswith('payload_path = Path(__file__).resolve().parents[3] / "reports" /') and 'prediction_dashboard_payload.json' in lines[i]:
        out.append('        payload_path = Path(__file__).resolve().parents[3] / "reports" / "ebola_historical_and_real_influenza_region_week_pipeline" / "prediction_dashboard_payload.json"')
        out.append('        if not payload_path.exists():')
        out.append('            payload_path = Path(__file__).resolve().parents[3] / "reports" / "ebola_data_visualization_prediction_engineering_loop" / "prediction_dashboard_demo_payload.json"')
        i += 1
        while i < len(lines) and (('payload_path = Path(__file__).resolve().parents[3] / "reports" /' in lines[i]) or lines[i].strip() == 'if not payload_path.exists():'):
            i += 1
        continue
    out.append(lines[i]); i += 1
APP.write_text('\n'.join(out)+'\n',encoding='utf-8')
after=sha(APP)
REPORT.mkdir(parents=True,exist_ok=True)
(REPORT/'dashboard_patch_report.json').write_text(json.dumps({'status':'P1_16_DASHBOARD_PATCH_FIXED','before_sha256':before,'after_sha256':after,'changed':before!=after,'created_at_utc':dt.datetime.now(dt.timezone.utc).isoformat()},ensure_ascii=False,indent=2,sort_keys=True)+'\n',encoding='utf-8')
print(json.dumps({'status':'P1_16_DASHBOARD_PATCH_FIXED','changed':before!=after},sort_keys=True))
