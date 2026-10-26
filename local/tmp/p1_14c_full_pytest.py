from pathlib import Path
import sys, json
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import a100_p1_12 as helper
client,_=helper.p111.p110r3.previous.p19.a100.connect()
try:
 _,project=helper.p111.p110r3.previous.p19.a100.paths(client)
 inner='cd '+helper.q(project)+'; source scripts/database/_env.sh >/dev/null 2>&1; unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; python -m pytest -q'
 rc,out,err=helper.p111.p110r3.previous.p19.a100.run(client,f"bash -lc {helper.q(inner)}",timeout=1800)
 print(json.dumps({'rc':rc,'stdout_tail':out[-12000:],'stderr_tail':err[-8000:]}, ensure_ascii=False))
 if rc: raise SystemExit(rc)
finally:
 client.close()
