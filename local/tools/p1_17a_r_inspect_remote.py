from __future__ import annotations
import json
from pathlib import Path
base=Path('reports/p1_17a_flunet_prediction_mvp_repair/43b29c05-3758-5406-a30d-d018ad89add7')
roots=sorted([p for p in base.iterdir() if p.is_dir()], key=lambda p:p.stat().st_mtime)
root=roots[-1]
print('LATEST_REPAIR_DIR', root)
paths=[
 root/'gate_results.json',
 root/'commands/http_authenticated_api.stderr.txt',
 root/'commands/http_authenticated_api.stdout.txt',
 root/'commands/real_browser_ui_acceptance.stderr.txt',
 root/'commands/real_browser_ui_acceptance.stdout.txt',
 root/'http_evidence/authenticated/authenticated_api_summary.json',
 root/'ui_acceptance/ui_acceptance_summary.json',
 root/'EXECUTION_STATE.json',
]
for p in paths:
 print('---', p)
 if p.exists():
  text=p.read_text(encoding='utf-8', errors='replace')
  if p.name=='gate_results.json':
   data=json.loads(text)
   print(json.dumps([{'gate_id':g['gate_id'],'status':g['status'],'blocker':g.get('blocker_code'),'computed':g.get('computed_value')} for g in data if g['status']!='PASS'], ensure_ascii=False, indent=2, default=str))
  elif p.name=='EXECUTION_STATE.json':
   data=json.loads(text)
   print(json.dumps({'final_status':data.get('final_status'),'ui':data.get('ui'),'http':data.get('http'),'unresolved_blockers':data.get('unresolved_blockers')}, ensure_ascii=False, indent=2, default=str))
  else:
   print(text[:5000])
 else:
  print('MISSING')
