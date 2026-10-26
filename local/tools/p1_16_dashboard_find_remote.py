from __future__ import annotations
import os
from pathlib import Path
PROJECT=Path(os.environ['PROJECT'])
for rel in ['src/epidemic_core/dashboard/app.py','src/epidemic_core/dashboard/static/dashboard.prediction-demo.js']:
 p=PROJECT/rel
 print('---',rel)
 if not p.exists():
  print('missing'); continue
 for i,line in enumerate(p.read_text(encoding='utf-8').splitlines(),1):
  if any(s in line for s in ['prediction-demo','prediction_demo_api','SAMPLE_DATA','PIPELINE_SMOKE','prediction_dashboard_demo_payload']):
   print(i,line[:240])
