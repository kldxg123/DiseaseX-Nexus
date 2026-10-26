from pathlib import Path
import sys, hashlib, json
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import a100_p1_12 as helper
ROOT=Path('remote_edit/p1_12_current')
files=['src/epidemic_core/ebola/fetcher.py','src/epidemic_core/p1_12/discovery.py','tests/unit/test_p1_14c_cdc_stacks_xlsx.py','tests/unit/test_p1_14c_v2_preview_path_grants.py']
client,_=helper.p111.p110r3.previous.p19.a100.connect()
try:
 _,project=helper.p111.p110r3.previous.p19.a100.paths(client)
 with client.open_sftp() as sftp:
  for rel in files:
   sftp.put(str(ROOT/rel), f'{project}/{rel}')
 print(json.dumps({'uploaded':files,'sha256':{rel:hashlib.sha256((ROOT/rel).read_bytes()).hexdigest() for rel in files}}, indent=2))
finally:
 client.close()
