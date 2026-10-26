from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import a100_p1_12 as helper
client,_=helper.p111.p110r3.previous.p19.a100.connect()
try:
 out=Path('reports/p1_14c_v2_preview_path'); out.mkdir(parents=True,exist_ok=True)
 with client.open_sftp() as sftp:
  for name in ['final_audit.json','materialization.json','migration_isolation.json','production_migration_apply.json','app_runtime_diagnosis.json','diagnosis.json','diagnosis.md']:
   try: sftp.get('/tmp/p1_14c_v2_preview_path/'+name, str(out/name))
   except Exception: pass
 print(out.resolve())
finally:
 client.close()
