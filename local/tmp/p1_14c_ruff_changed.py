from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import a100_p1_12 as helper
client,_=helper.p111.p110r3.previous.p19.a100.connect()
try:
 _,project=helper.p111.p110r3.previous.p19.a100.paths(client)
 cmd='cd '+helper.q(project)+'; source scripts/database/_env.sh >/dev/null 2>&1; python -m ruff check src/epidemic_core/ebola/fetcher.py src/epidemic_core/p1_12/discovery.py src/epidemic_core/p1_10/diagnostics.py migrations/versions/0027_p1_14c_v2_preview_path_grants.py tests/unit/test_p1_14c_cdc_stacks_xlsx.py tests/unit/test_p1_14c_v2_preview_path_grants.py'
 rc,out,err=helper.p111.p110r3.previous.p19.a100.run(client,f"bash -lc {helper.q(cmd)}",timeout=300)
 print(out); print(err,file=sys.stderr)
 if rc: raise SystemExit(rc)
finally:
 client.close()
