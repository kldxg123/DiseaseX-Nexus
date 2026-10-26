from pathlib import Path
import sys,json
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import a100_p1_12 as helper
REMOTE=r'''
import os,json,pathlib,datetime,hashlib
from sqlalchemy import create_engine,text
OUT=pathlib.Path('/tmp/p1_14d_authenticated_ui_acceptance')
engine=create_engine(os.environ['APP_DATABASE_URL'],hide_parameters=True,pool_pre_ping=True)
def digest(v): return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':'),default=str).encode()).hexdigest()
with engine.connect() as c:
 c.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'))
 c.execute(text("SET LOCAL statement_timeout='30s'"))
 rows=[dict(r) for r in c.execute(text("""
  SELECT id::text, created_at::text, left(to_jsonb(q)::text,500) row_prefix, CASE WHEN to_jsonb(q)::text ILIKE '%pytest%' OR to_jsonb(q)::text ILIKE '%fixture%' OR to_jsonb(q)::text ILIKE '%test%' THEN true ELSE false END has_test_marker FROM epidemic.query_requests q ORDER BY created_at,id
 """)).mappings().all()]
 out={'generated_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'query_requests_count':len(rows),'rows':rows,'digest':digest(rows)}
(OUT/'production_test_contamination_probe.json').write_text(json.dumps(out,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
print(json.dumps({'count':len(rows),'has_test_marker_count':sum(1 for r in rows if r['has_test_marker']),'first':rows[0] if rows else None,'last':rows[-1] if rows else None},ensure_ascii=False))
'''
client,_=helper.p111.p110r3.previous.p19.a100.connect()
try:
 _,project=helper.p111.p110r3.previous.p19.a100.paths(client)
 remote='/tmp/p1_14d_authenticated_ui_acceptance/contamination_probe.py'
 with client.open_sftp() as sftp:
  with sftp.open(remote,'w') as f:f.write(REMOTE)
 inner='cd '+helper.q(project)+'; source scripts/database/_env.sh >/dev/null 2>&1; P1_14D_PROJECT='+helper.q(project)+' python3 '+helper.q(remote)
 rc,out,err=helper.p111.p110r3.previous.p19.a100.run(client,f"bash -lc {helper.q(inner)}",timeout=120)
 print(out)
 if err: print(err,file=sys.stderr)
 if rc: raise SystemExit(rc)
finally:
 client.close()

