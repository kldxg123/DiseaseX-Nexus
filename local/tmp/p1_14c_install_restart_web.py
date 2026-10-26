from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import a100_p1_12 as helper
client,_=helper.p111.p110r3.previous.p19.a100.connect()
try:
 _,project=helper.p111.p110r3.previous.p19.a100.paths(client)
 inner='cd '+helper.q(project)+'; unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; bash scripts/source_review/install_runtime.sh; bash scripts/dashboard/install_runtime.sh; bash scripts/source_review/stop_source_review.sh || true; bash scripts/dashboard/stop_dashboard.sh || true; bash scripts/source_review/start_source_review.sh; bash scripts/dashboard/start_dashboard.sh; curl --silent --show-error --noproxy "*" --max-time 5 http://127.0.0.1:8021/readyz; echo; curl --silent --show-error --noproxy "*" --max-time 5 http://127.0.0.1:8020/readyz; echo'
 rc,out,err=helper.p111.p110r3.previous.p19.a100.run(client,f"bash -lc {helper.q(inner)}",timeout=600)
 print(out); print(err,file=sys.stderr)
 if rc: raise SystemExit(rc)
finally:
 client.close()
