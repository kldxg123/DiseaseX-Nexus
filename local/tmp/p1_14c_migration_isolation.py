from pathlib import Path
import sys, json
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import a100_p1_12 as helper
REMOTE=r'''
import os, json, urllib.parse, secrets, subprocess, pathlib, time
from sqlalchemy import create_engine, text
PROJECT=pathlib.Path(os.environ['P1_14C_PROJECT'])
base=os.environ['MIGRATION_DATABASE_URL']
parsed=urllib.parse.urlsplit(base)
test_db='p1_14c_acl_test_'+secrets.token_hex(4)
admin_user=os.environ.get('POSTGRES_ADMIN_USER')
admin_password=os.environ.get('POSTGRES_ADMIN_PASSWORD')
host=os.environ.get('POSTGRES_HOST','127.0.0.1')
port=os.environ.get('POSTGRES_PORT','5432')
admin_url=urllib.parse.urlunsplit((parsed.scheme, f"{urllib.parse.quote(admin_user)}:{urllib.parse.quote(admin_password)}@{host}:{port}", 'postgres', '', ''))
test_url=urllib.parse.urlunsplit((parsed.scheme, f"{urllib.parse.quote(admin_user)}:{urllib.parse.quote(admin_password)}@{host}:{port}", test_db, '', ''))
out={'test_db':'<temp>', 'steps':[]}
def run(cmd, env=None, timeout=600):
 p=subprocess.run(cmd, shell=True, cwd=PROJECT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, timeout=timeout, executable='/bin/bash')
 return {'rc':p.returncode,'stdout':p.stdout[-4000:],'stderr':p.stderr[-4000:]}
admin=create_engine(admin_url, isolation_level='AUTOCOMMIT', hide_parameters=True)
try:
 with admin.connect() as c:
  c.execute(text(f'CREATE DATABASE {test_db}'))
 out['steps'].append({'create_database':True})
 env=os.environ.copy(); env['MIGRATION_DATABASE_URL']=test_url; env['APP_DATABASE_URL']=test_url; env['PYTHONPATH']=str(PROJECT/'src')
 setup_engine=create_engine(test_url, isolation_level='AUTOCOMMIT', hide_parameters=True)
 with setup_engine.connect() as c:
  c.execute(text('CREATE SCHEMA IF NOT EXISTS epidemic'))
 out['steps'].append({'bootstrap_schema_epidemic': True})
 r=run('alembic upgrade head', env, 1200); out['steps'].append({'upgrade_head':r})
 if r['rc']!=0: raise SystemExit(json.dumps(out))
 e=create_engine(test_url, hide_parameters=True)
 with e.connect() as c:
  rev1=c.execute(text('SELECT version_num FROM epidemic.alembic_version')).scalar()
  counts1=[]
  for sql in ["SELECT count(*) FROM epidemic_discovery.source_review_cases", "SELECT count(*) FROM epidemic_p1_12.candidate_preview_rows_v2", "SELECT has_function_privilege('epidemic_app','epidemic_p1_12.review_ready_v2_violations(uuid)','EXECUTE')"]:
   counts1.append(str(c.execute(text(sql)).scalar()))
 out['after_upgrade']={'revision':rev1,'business_counts':counts1[:2],'execute_privilege':counts1[2]}
 r=run('alembic downgrade 0026_p1_12_preview_view_grants', env, 600); out['steps'].append({'downgrade_0026':r})
 if r['rc']!=0: raise SystemExit(json.dumps(out))
 with e.connect() as c:
  rev2=c.execute(text('SELECT version_num FROM epidemic.alembic_version')).scalar()
  priv2=c.execute(text("SELECT has_function_privilege('epidemic_app','epidemic_p1_12.review_ready_v2_violations(uuid)','EXECUTE')")).scalar()
 out['after_downgrade']={'revision':rev2,'execute_privilege':str(priv2)}
 r=run('alembic upgrade head', env, 600); out['steps'].append({'reupgrade_head':r})
 if r['rc']!=0: raise SystemExit(json.dumps(out))
 with e.connect() as c:
  rev3=c.execute(text('SELECT version_num FROM epidemic.alembic_version')).scalar()
  counts3=[str(c.execute(text("SELECT count(*) FROM epidemic_discovery.source_review_cases")).scalar()), str(c.execute(text("SELECT count(*) FROM epidemic_p1_12.candidate_preview_rows_v2")).scalar())]
  priv3=c.execute(text("SELECT has_function_privilege('epidemic_app','epidemic_p1_12.review_ready_v2_violations(uuid)','EXECUTE')")).scalar()
 out['after_reupgrade']={'revision':rev3,'business_counts':counts3,'execute_privilege':str(priv3)}
 out['passed']= rev3=='0027_p1_14c_v2_preview_path_grants' and str(priv3)=='True' and counts3==['0','0']
finally:
 try:
  with admin.connect() as c:
   c.execute(text(f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='{test_db}'"))
   c.execute(text(f'DROP DATABASE IF EXISTS {test_db}'))
  out['dropped']=True
 except Exception as e:
  out['drop_error']=str(e)[:500]
 pathlib.Path('/tmp/p1_14c_v2_preview_path/migration_isolation.json').write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
 print(json.dumps({'passed':out.get('passed'), 'after_reupgrade':out.get('after_reupgrade'), 'dropped':out.get('dropped'), 'step_count':len(out.get('steps',[]))}, ensure_ascii=False, default=str))
'''
client,_=helper.p111.p110r3.previous.p19.a100.connect()
try:
    _,project=helper.p111.p110r3.previous.p19.a100.paths(client)
    remote='/tmp/p1_14c_v2_preview_path/migration_isolation.py'
    with client.open_sftp() as sftp:
        with sftp.open(remote,'w') as f: f.write(REMOTE)
    inner = (
        "cd "
        + helper.q(project)
        + "; set -a; source .secrets/postgres.env; source .secrets/p1_1.env; set +a; "
        + "export PATH=../conda/envs/epidemic-core/bin:$PATH; export PYTHONPATH=src; "
        + "APP_DATABASE_URL=\"$MIGRATION_DATABASE_URL\" P1_14C_PROJECT="
        + helper.q(project)
        + " python3 "
        + helper.q(remote)
    )
    cmd=f"bash -lc {helper.q(inner)}"
    rc,out,err=helper.p111.p110r3.previous.p19.a100.run(client,cmd,timeout=1800)
    print(out)
    if err: print(err, file=sys.stderr)
    if rc!=0: raise SystemExit(rc)
finally:
    client.close()


