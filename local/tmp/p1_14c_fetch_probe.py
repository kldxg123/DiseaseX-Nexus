from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import a100_p1_12 as helper
client,_=helper.p111.p110r3.previous.p19.a100.connect()
try:
    _,project=helper.p111.p110r3.previous.p19.a100.paths(client)
    code="""
import os, json
from epidemic_core.ebola.fetcher import OfficialSourceFetcher
f=OfficialSourceFetcher({'stacks.cdc.gov'})
for url in ['https://stacks.cdc.gov/robots.txt','https://stacks.cdc.gov/view/cdc/37575/cdc_37575_DS2.xlsx']:
 try:
  r=f.fetch(url, check_robots=(not url.endswith('robots.txt')))
  print(json.dumps({'url':url,'ok':True,'status':r.status,'mime':r.mime_type,'bytes':len(r.payload),'sha':r.sha256}))
 except Exception as e:
  print(json.dumps({'url':url,'ok':False,'type':type(e).__name__,'error':str(e)}))
"""
    remote='/tmp/p1_14c_v2_preview_path/fetch_probe.py'
    with client.open_sftp() as sftp:
        with sftp.open(remote,'w') as f: f.write(code)
    cmd=f"bash -lc {helper.q('cd '+helper.q(project)+'; source scripts/database/_env.sh >/dev/null 2>&1; python3 '+helper.q(remote))}"
    rc,out,err=helper.p111.p110r3.previous.p19.a100.run(client,cmd,timeout=180)
    print(out)
    if err: print(err, file=sys.stderr)
finally:
    client.close()
