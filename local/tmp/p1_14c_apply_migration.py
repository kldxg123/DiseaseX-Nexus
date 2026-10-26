from pathlib import Path
import sys, json
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import a100_p1_12 as helper
client,_=helper.p111.p110r3.previous.p19.a100.connect()
try:
    _,project=helper.p111.p110r3.previous.p19.a100.paths(client)
    inner='cd '+helper.q(project)+'; source scripts/database/_env.sh >/dev/null 2>&1; before=$(alembic current); alembic upgrade head; after=$(alembic current); printf "BEFORE:%s\nAFTER:%s\n" "$before" "$after"'
    rc,out,err=helper.p111.p110r3.previous.p19.a100.run(client, f"bash -lc {helper.q(inner)}", timeout=600)
    print(out)
    if err: print(err, file=sys.stderr)
    if rc!=0: raise SystemExit(rc)
finally:
    client.close()
