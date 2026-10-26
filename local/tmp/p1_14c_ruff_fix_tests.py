from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import a100_p1_12 as helper
client,_=helper.p111.p110r3.previous.p19.a100.connect()
try:
 _,project=helper.p111.p110r3.previous.p19.a100.paths(client)
 cmd='cd '+helper.q(project)+'; source scripts/database/_env.sh >/dev/null 2>&1; python -m ruff check --fix tests/unit/test_p1_14c_cdc_stacks_xlsx.py tests/unit/test_p1_14c_v2_preview_path_grants.py'
 rc,out,err=helper.p111.p110r3.previous.p19.a100.run(client,f"bash -lc {helper.q(cmd)}",timeout=300)
 print(out); print(err,file=sys.stderr)
 with client.open_sftp() as sftp:
  for rel in ['tests/unit/test_p1_14c_cdc_stacks_xlsx.py','tests/unit/test_p1_14c_v2_preview_path_grants.py']:
   local=Path('remote_edit/p1_12_current')/rel
   sftp.get(f'{project}/{rel}', str(local))
 if rc: raise SystemExit(rc)
finally:
 client.close()
