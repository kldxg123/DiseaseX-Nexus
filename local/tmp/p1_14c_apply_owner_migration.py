from pathlib import Path
import sys, json
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import a100_p1_12 as helper
REMOTE=r'''
import os, urllib.parse, subprocess, pathlib, json
PROJECT=pathlib.Path(os.environ['P1_14C_PROJECT'])
parsed=urllib.parse.urlsplit(os.environ['MIGRATION_DATABASE_URL'])
admin_user=os.environ['POSTGRES_ADMIN_USER']; admin_password=os.environ['POSTGRES_ADMIN_PASSWORD']
host=os.environ.get('POSTGRES_HOST','127.0.0.1'); port=os.environ.get('POSTGRES_PORT','5432')
db=parsed.path.lstrip('/')
owner_url=urllib.parse.urlunsplit((parsed.scheme, f"{urllib.parse.quote(admin_user)}:{urllib.parse.quote(admin_password)}@{host}:{port}", db, '', ''))
def run(cmd, env):
 p=subprocess.run(cmd, shell=True, cwd=PROJECT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, timeout=600, executable='/bin/bash')
 return {'rc':p.returncode,'stdout':p.stdout[-4000:],'stderr':p.stderr[-4000:]}
env=os.environ.copy(); env['MIGRATION_DATABASE_URL']=owner_url; env['APP_DATABASE_URL']=owner_url; env['PYTHONPATH']=str(PROJECT/'src')
res={'before':run('alembic current', env)}
res['upgrade']=run('alembic upgrade head', env)
res['after']=run('alembic current', env)
path=pathlib.Path('/tmp/p1_14c_v2_preview_path/production_migration_apply.json')
path.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({'before':res['before']['stdout'].strip(), 'upgrade_rc':res['upgrade']['rc'], 'after':res['after']['stdout'].strip()}, ensure_ascii=False))
if res['upgrade']['rc']!=0: raise SystemExit(1)
'''
client,_=helper.p111.p110r3.previous.p19.a100.connect()
try:
    _,project=helper.p111.p110r3.previous.p19.a100.paths(client)
    remote='/tmp/p1_14c_v2_preview_path/apply_owner_migration.py'
    with client.open_sftp() as sftp:
        with sftp.open(remote,'w') as f:f.write(REMOTE)
    inner='cd '+helper.q(project)+'; set -a; source .secrets/postgres.env; source .secrets/p1_1.env; set +a; export PATH=../conda/envs/epidemic-core/bin:$PATH; P1_14C_PROJECT='+helper.q(project)+' python3 '+helper.q(remote)
    rc,out,err=helper.p111.p110r3.previous.p19.a100.run(client, f"bash -lc {helper.q(inner)}", timeout=900)
    print(out)
    if err: print(err, file=sys.stderr)
    if rc!=0: raise SystemExit(rc)
finally:
    client.close()
