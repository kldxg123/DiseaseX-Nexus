from pathlib import Path
import sys,json
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import a100_p1_12 as helper
client,_=helper.p111.p110r3.previous.p19.a100.connect()
try:
 _,project=helper.p111.p110r3.previous.p19.a100.paths(client)
 inner='cd '+helper.q(project)+'; source scripts/database/_env.sh >/dev/null 2>&1; export PYTHONPATH=src; python -m pytest tests/test_p1_11_ebola_parsers.py tests/unit/test_p1_14c_cdc_stacks_xlsx.py tests/unit/test_p1_14c_v2_preview_path_grants.py tests/unit/test_p1_12_review_gate.py tests/unit/test_p1_12_trusted_and_permissions.py -q'
 rc,out,err=helper.p111.p110r3.previous.p19.a100.run(client,f"bash -lc {helper.q(inner)}",timeout=600)
 print(json.dumps({'rc':rc,'stdout':out[-8000:],'stderr':err[-4000:]},ensure_ascii=False))
 if rc: raise SystemExit(rc)
finally:
 client.close()
