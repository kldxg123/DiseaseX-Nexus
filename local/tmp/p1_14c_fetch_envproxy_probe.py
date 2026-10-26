from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import a100_p1_12 as helper
client,_=helper.p111.p110r3.previous.p19.a100.connect()
try:
    _,project=helper.p111.p110r3.previous.p19.a100.paths(client)
    code="""
import json, os
from epidemic_core.ebola.fetcher import OfficialSourceFetcher
for proxy in [False, True]:
 f=OfficialSourceFetcher({'stacks.cdc.gov'}, user_agent='python-urllib/3.12', use_environment_proxy=proxy)
 try:
  r=f.fetch('https://stacks.cdc.gov/view/cdc/37575/cdc_37575_DS2.xlsx', check_robots=False)
  print(json.dumps({'proxy':proxy,'ok':True,'status':r.status,'mime':r.mime_type,'bytes':len(r.payload),'sha':r.sha256}))
 except Exception as e:
  print(json.dumps({'proxy':proxy,'ok':False,'type':type(e).__name__,'error':str(e)}))
"""
    remote='/tmp/p1_14c_v2_preview_path/fetch_envproxy_probe.py'
    with client.open_sftp() as sftp:
        with sftp.open(remote,'w') as f:f.write(code)
    inner = "cd " + helper.q(project) + "; export PYTHONPATH=src; python3 " + helper.q(remote)
    rc,out,err=helper.p111.p110r3.previous.p19.a100.run(client,f"bash -lc {helper.q(inner)}",timeout=120)
    print(out); print(err, file=sys.stderr)
finally:
    client.close()

