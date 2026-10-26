from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import a100_p1_12 as helper
client,_=helper.p111.p110r3.previous.p19.a100.connect()
try:
 out=Path('reports/p1_14d_authenticated_ui_acceptance')
 out.mkdir(parents=True,exist_ok=True)
 with client.open_sftp() as sftp:
  for name in ['report.json','report.md','database_before.json','p1_14c_closeout.json','production_test_contamination_probe.json']:
   sftp.get('/tmp/p1_14d_authenticated_ui_acceptance/'+name, str(out/name))
 print(out.resolve())
finally:
 client.close()
