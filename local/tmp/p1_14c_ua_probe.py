from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import a100_p1_12 as helper
client,_=helper.p111.p110r3.previous.p19.a100.connect()
try:
    _,project=helper.p111.p110r3.previous.p19.a100.paths(client)
    code=r'''
import urllib.request, json, ssl
url='https://stacks.cdc.gov/view/cdc/37575/cdc_37575_DS2.xlsx'
headers_list=[
 {'User-Agent':'epidemic-openclaw-p1-14c/1.0','Accept':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,*/*;q=0.1'},
 {'User-Agent':'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36','Accept':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/octet-stream,*/*;q=0.1','Referer':'https://stacks.cdc.gov/view/cdc/37575'},
 {'User-Agent':'python-urllib/3.12'},
]
for h in headers_list:
 try:
  req=urllib.request.Request(url,headers={**h,'Accept-Encoding':'identity'})
  with urllib.request.urlopen(req,context=ssl.create_default_context(),timeout=30) as r:
   data=r.read(20)
   print(json.dumps({'ua':h['User-Agent'][:20],'ok':True,'status':r.status,'mime':r.headers.get_content_type(),'head':data.hex()}))
 except Exception as e:
  print(json.dumps({'ua':h['User-Agent'][:20],'ok':False,'type':type(e).__name__,'error':str(e)[:200]}))
'''
    remote='/tmp/p1_14c_v2_preview_path/ua_probe.py'
    with client.open_sftp() as sftp:
        with sftp.open(remote,'w') as f:f.write(code)
    rc,out,err=helper.p111.p110r3.previous.p19.a100.run(client,f"python3 {helper.q(remote)}",timeout=120)
    print(out)
finally:
    client.close()
