from __future__ import annotations
import os, json, hashlib, datetime as dt
from pathlib import Path
PROJECT=Path(os.environ['PROJECT']); REPORT=PROJECT/'reports/ebola_historical_and_real_influenza_region_week_pipeline'; APP=PROJECT/'src/epidemic_core/dashboard/app.py'
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
before=sha(APP); text=APP.read_text(encoding='utf-8')
marker='<div id="prediction-demo-status" class="notice">'
warning='<p class="preview-warning">REAL OFFICIAL DATA / WHO FluNet / RETROSPECTIVE ENGINEERING DEMO / NOT A VALIDATED PUBLIC-HEALTH WARNING SYSTEM / DOES NOT VALIDATE EBOLA PREDICTION.</p>\n'
if 'WHO FluNet / RETROSPECTIVE ENGINEERING DEMO' not in text and marker in text:
    text=text.replace(marker, warning+marker, 1)
APP.write_text(text,encoding='utf-8')
after=sha(APP); REPORT.mkdir(parents=True,exist_ok=True)
(REPORT/'dashboard_patch_report.json').write_text(json.dumps({'status':'P1_16_DASHBOARD_VISIBLE_WARNING_PATCHED','before_sha256':before,'after_sha256':after,'changed':before!=after,'created_at_utc':dt.datetime.now(dt.timezone.utc).isoformat()},ensure_ascii=False,indent=2,sort_keys=True)+'\n',encoding='utf-8')
print(json.dumps({'status':'P1_16_DASHBOARD_VISIBLE_WARNING_PATCHED','changed':before!=after},sort_keys=True))
