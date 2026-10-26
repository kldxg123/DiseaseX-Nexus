from __future__ import annotations
import json,os,sys,urllib.request
request=urllib.request.Request("http://127.0.0.1:8010"+sys.argv[1],headers={"Authorization":"Bearer "+os.environ["EPIDEMIC_QUERY_API_TOKEN"]})
with urllib.request.urlopen(request,timeout=5) as response:
    data=json.load(response)
if not data:raise SystemExit(1)
