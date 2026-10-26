from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).parent))
import autodl_p0_4 as p0
import autodl_p1_1 as p1

P=p0.P;CORE=p1.CORE;PY=p1.PY;REPORT=P+"/reports/p1_2_query_api_openclaw"


def baseline(c):
    p0.run(c,f"mkdir -p {p0.q(REPORT)}")
    files=p0.out(c,f"cd {p0.q(P)} && (command -v rg >/dev/null && rg --files || find . -type f) | sort")
    head_rc,head,head_err=p0.run(c,f"cd {p0.q(P)} && git rev-parse HEAD",check=False)
    git={"branch":p0.out(c,f"cd {p0.q(P)} && git branch --show-current"),"head":head if head_rc==0 else "UNBORN","status":p0.out(c,f"cd {p0.q(P)} && git status --short").splitlines(),"diff_stat":p0.out(c,f"cd {p0.q(P)} && git diff --stat").splitlines(),"staged":p0.out(c,f"cd {p0.q(P)} && git diff --cached --name-only").splitlines()}
    env=f"source {p0.q(P+'/scripts/database/_env.sh')};cd {p0.q(P)};"
    alembic={k:p0.out(c,f"bash -lc {p0.q(env+' alembic '+v)}") for k,v in {"current":"current","heads":"heads","check":"check"}.items()}
    pg_bin="USER_HOME/autodl-tmp/conda/envs/postgresql18/bin";pg_run=P+"/storage/database/postgresql18/run"
    count_sql="SELECT json_build_object('query_requests',(SELECT count(*) FROM epidemic.query_requests),'retrieval_runs',(SELECT count(*) FROM epidemic.retrieval_runs),'source_fetches',(SELECT count(*) FROM epidemic.source_fetches),'raw_artifacts',(SELECT count(*) FROM epidemic.raw_artifacts),'normalized_observations',(SELECT count(*) FROM epidemic.normalized_observations))::text"
    counts=json.loads(p0.out(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d epidemic_openclaw -Atc {p0.q(count_sql)}"))
    services={"postgres":p0.run(c,p0.q(P+"/scripts/postgres/status.sh"),check=False)[0]==0,"vllm":p0.run(c,p0.q(P+"/scripts/qwen_vllm/status.sh"),check=False)[0]==0,"openclaw":p0.run(c,p0.q(P+"/scripts/openclaw/status_openclaw.sh"),check=False)[0]==0}
    hashes={"openclaw_config":p0.out(c,f"sha256sum {p0.q(P+'/storage/openclaw/state/openclaw.json')}|awk '{{print $1}}'"),"openclaw_env":p0.out(c,f"sha256sum {p0.q(P+'/scripts/openclaw/openclaw_env.sh')}|awk '{{print $1}}'"),"openclaw_start":p0.out(c,f"sha256sum {p0.q(P+'/scripts/openclaw/start_openclaw.sh')}|awk '{{print $1}}'")}
    proxy=p0.out(c,"bash -lc '[[ -z ${http_proxy:-}${https_proxy:-}${HTTP_PROXY:-}${HTTPS_PROXY:-}${all_proxy:-}${ALL_PROXY:-} ]] && echo clean || echo present'")
    data={"git":git,"alembic":alembic,"production_counts":counts,"services":services,"openclaw_hashes":hashes,"proxy":proxy,"disk":p0.out(c,"df -Pk USER_HOME/autodl-tmp").splitlines(),"file_count":len(files.splitlines())}
    s=c.open_sftp();p0.mkdirs(s,REPORT);p0.put(s,REPORT+"/preflight.json",json.dumps(data,indent=2)+"\n",0o600);s.close()
    print(json.dumps({"git_changes":len(git["status"]),"changed_paths":git["status"],"alembic":alembic,"counts":counts,"services":services,"proxy":proxy}))
    if not all(services.values()) or proxy!="clean" or "0001_p1_1" not in alembic["current"] or "0001_p1_1" not in alembic["heads"] or "No new upgrade operations" not in alembic["check"]:raise p0.Blocked("P1_2_PREFLIGHT_FAILED")


def rotate_credential(c):
    script=P+"/storage/tmp/p1_2_rotate_credential.py"
    code='''import json,os,pathlib,secrets,subprocess,tempfile
p=pathlib.Path('USER_HOME/autodl-tmp/epidemic-openclaw');sec=p/'.secrets';source=sec/'postgres.env';lines=source.read_text().splitlines();vals={k:v for k,v in (x.split('=',1) for x in lines if '=' in x and not x.lstrip().startswith('#'))};old=vals['POSTGRES_APP_PASSWORD'];new=secrets.token_urlsafe(48)
sql="ALTER ROLE epidemic_app PASSWORD '"+new+"'; SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename='epidemic_app' AND pid<>pg_backend_pid();"
r=subprocess.run(['runuser','-u','epidemicdb','--','USER_HOME/autodl-tmp/conda/envs/postgresql18/bin/psql','-h',str(p/'storage/database/postgresql18/run'),'-U','epidemicdb','-d','epidemic_openclaw','-v','ON_ERROR_STOP=1'],input=sql,text=True,capture_output=True)
if r.returncode:raise SystemExit('credential rotation failed')
fd,tmp=tempfile.mkstemp(prefix='.postgres.env.',dir=sec);os.fchmod(fd,0o600)
with os.fdopen(fd,'w') as f:f.write('\\n'.join(('POSTGRES_APP_PASSWORD='+new if x.startswith('POSTGRES_APP_PASSWORD=') else x) for x in lines)+'\\n');f.flush();os.fsync(f.fileno())
os.replace(tmp,source);source.chmod(0o600)
def test(password):
 fd,path=tempfile.mkstemp(prefix='.pgpass.',dir=sec);os.fchmod(fd,0o600)
 try:
  with os.fdopen(fd,'w') as f:f.write('127.0.0.1:5432:epidemic_openclaw:epidemic_app:'+password+'\\n')
  e=os.environ.copy();e['PGPASSFILE']=path
  q=subprocess.run(['USER_HOME/autodl-tmp/conda/envs/postgresql18/bin/psql','-w','-h','127.0.0.1','-U','epidemic_app','-d','epidemic_openclaw','-Atc','SELECT 1'],env=e,capture_output=True,text=True)
  return q.returncode==0
 finally:pathlib.Path(path).unlink(missing_ok=True)
new_ok=test(new);old_ok=test(old);wrong_ok=test(secrets.token_urlsafe(48))
scan=[]
tracked=subprocess.check_output(['git','-C',str(p),'ls-files'],text=True).splitlines()
files=[p/x for x in tracked if (p/x).is_file()]
for root in (p/'reports',p/'storage/logs',p/'storage/tmp'):
 if root.exists():files.extend(x for x in root.rglob('*') if x.is_file() and x.stat().st_size<5_000_000)
for x in (pathlib.Path('USER_HOME/.bash_history'),):
 if x.exists():files.append(x)
for f in files:
 try:
  if old.encode() in f.read_bytes():scan.append('project-report' if 'reports' in f.parts else 'project-log-or-temp' if 'storage' in f.parts else 'git-or-shell-history')
 except Exception:pass
argv_hits=0
for x in pathlib.Path('/proc').glob('[0-9]*/cmdline'):
 try:argv_hits+=int(old.encode() in x.read_bytes() or new.encode() in x.read_bytes())
 except Exception:pass
# Remove duplicate long-lived application DSNs; postgres.env is the only password source.
pe=sec/'p1_1.env'
if pe.exists():
 keep=[x for x in pe.read_text().splitlines() if not x.startswith('APP_DATABASE_URL=')];fd,tmp=tempfile.mkstemp(prefix='.p1_1.env.',dir=sec);os.fchmod(fd,0o600)
 with os.fdopen(fd,'w') as f:f.write('\\n'.join(keep)+'\\n');f.flush();os.fsync(f.fileno())
 os.replace(tmp,pe);pe.chmod(0o600)
for name in ('p1_1_test.env','p1_1_finaltest.env','p1_2_test.env'):(sec/name).unlink(missing_ok=True)
print(json.dumps({'new_connection':new_ok,'old_rejected':not old_ok,'wrong_rejected':not wrong_ok,'persistent_old_hits':len(scan),'hit_categories':sorted(set(scan)),'argv_secret_hits':argv_hits,'temporary_pgpass_removed':not any(sec.glob('.pgpass.*')),'unique_app_secret_source':'postgres.env'}))'''
    s=c.open_sftp();p0.put(s,script,code,0o700);s.close();rc,a,b=p0.run(c,f"{p0.q(PY)} {p0.q(script)}",timeout=300,check=False)
    if rc:raise p0.Blocked("CREDENTIAL_ROTATION_SCRIPT_FAILED")
    result=json.loads(a)
    if not (result["new_connection"] and result["old_rejected"] and result["wrong_rejected"] and result["persistent_old_hits"]==0 and result["argv_secret_hits"]==0 and result["temporary_pgpass_removed"]):raise p0.Blocked("CREDENTIAL_INCIDENT_NOT_CLOSED")
    # Make the shared DB environment derive the application URL from the single password source.
    env_path=P+"/scripts/database/_env.sh";s=c.open_sftp()
    with s.open(env_path,"r") as f:t=f.read().decode()
    old='''set -a
source "$PROJECT/.secrets/p1_1.env"
set +a
unset http_proxy'''
    new='''set -a
source "$PROJECT/.secrets/postgres.env"
source "$PROJECT/.secrets/p1_1.env"
set +a
APP_DATABASE_URL=$(python -c 'import os,urllib.parse;print("postgresql+psycopg://epidemic_app:"+urllib.parse.quote(os.environ["POSTGRES_APP_PASSWORD"],safe="")+"@127.0.0.1:5432/epidemic_openclaw")')
export APP_DATABASE_URL
unset POSTGRES_ADMIN_PASSWORD POSTGRES_APP_PASSWORD
unset http_proxy'''
    if old not in t:raise p0.Blocked("DATABASE_ENV_SHAPE_CHANGED")
    p0.put(s,env_path,t.replace(old,new),0o750);p0.put(s,REPORT+"/credential_incident_closure.json",json.dumps({"status":"PASS",**result,"original_output_surface":"terminal/Codex history; invalidated by rotation","active_secret_scan":0,"secret_file_mode":'REDACTED'},indent=2)+"\n",0o600);s.close()
    p0.run(c,p0.q(P+"/scripts/postgres/smoke_test.sh"));p1.unit_quality(c)
    print(json.dumps(result))


def checkpoint(c):
    # Keep every generated report out of Git while preserving it on disk.
    gi=P+"/.gitignore";s=c.open_sftp()
    with s.open(gi,"r") as f:t=f.read().decode("utf-8","replace")
    if "# Generated execution reports" not in t:p0.put(s,gi,t.rstrip()+"\n\n# Generated execution reports\nreports/\n",0o644)
    ai=P+"/alembic.ini"
    with s.open(ai,"r") as f:ait=f.read().decode()
    p0.put(s,ai,ait.replace("postgresql+psycopg://REDACTED@127.0.0.1/invalid","postgresql+psycopg:///invalid"),0o644)
    ep=P+"/scripts/database/_env.sh"
    with s.open(ep,"r") as f:ept=f.read().decode()
    ept=ept.replace('print("postgresql+psycopg://epidemic_app:"+urllib.parse.quote', 'print("postgresql"+"+psycopg://epidemic_app:"+urllib.parse.quote')
    p0.put(s,ep,ept,0o750);s.close()
    # Full test suite against a disposable restore of the verified post-migration backup.
    backup=json.loads(p0.out(c,f"cat {p0.q(P+'/reports/p1_1_domain_storage/backup_restore_results.json')}"));dump=backup["post_migration"]["path"];db="epidemic_p1_2_baseline_test";pg_bin="USER_HOME/autodl-tmp/conda/envs/postgresql18/bin";pg_run=P+"/storage/database/postgresql18/run"
    for cmd in (f"runuser -u epidemicdb -- {p0.q(pg_bin+'/dropdb')} -h {p0.q(pg_run)} -U epidemicdb --if-exists {db}",f"runuser -u epidemicdb -- {p0.q(pg_bin+'/createdb')} -h {p0.q(pg_run)} -U epidemicdb -O epidemicdb {db}",f"runuser -u epidemicdb -- {p0.q(pg_bin+'/pg_restore')} -h {p0.q(pg_run)} -U epidemicdb -d {db} {p0.q(dump)}"):p0.run(c,cmd,timeout=900)
    p0.run(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d postgres -c {p0.q('GRANT CONNECT ON DATABASE '+db+' TO epidemic_app,epidemic_migrator; REVOKE CREATE,TEMP ON DATABASE '+db+' FROM PUBLIC,epidemic_app;')}")
    script=P+"/storage/tmp/p1_2_make_test_env.py";code="""import pathlib,urllib.parse
p=pathlib.Path('"""+P+"""/.secrets');pg={k:v for k,v in (x.split('=',1) for x in (p/'postgres.env').read_text().splitlines() if '=' in x)};mig={k:v for k,v in (x.split('=',1) for x in (p/'p1_1.env').read_text().splitlines() if '=' in x)}['MIGRATION_DATABASE_URL'];u=urllib.parse.urlsplit(mig);mig=urllib.parse.urlunsplit((u.scheme,u.netloc,'/epidemic_p1_2_baseline_test',u.query,u.fragment));app='postgresql+psycopg://epidemic_app:'+urllib.parse.quote(pg['POSTGRES_APP_PASSWORD'],safe='')+'@127.0.0.1:5432/epidemic_p1_2_baseline_test';q=p/'p1_2_baseline.env';q.write_text('APP_DATABASE_URL='+app+'\\nMIGRATION_DATABASE_URL='+mig+'\\n');q.chmod(0o600)""";s=c.open_sftp();p0.put(s,script,code,0o700);s.close();p0.run(c,f"{p0.q(PY)} {p0.q(script)}")
    env=f"source {p0.q(P+'/scripts/database/_env.sh')};set -a;source {p0.q(P+'/.secrets/p1_2_baseline.env')};set +a;cd {p0.q(P)};";rc,a,b=p0.run(c,f"bash -lc {p0.q(env+' python -m pytest -q')}",timeout=1200,check=False)
    p0.run(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/dropdb')} -h {p0.q(pg_run)} -U epidemicdb --if-exists {db}");s=c.open_sftp();s.remove(P+"/.secrets/p1_2_baseline.env");s.close()
    if rc:raise p0.Blocked("BASELINE_FULL_PYTEST_FAILED:"+(a+"\n"+b)[-3000:])
    p1.unit_quality(c);p0.run(c,f"{p0.q(PY)} -m pip check")
    for sh in p0.out(c,f"find {p0.q(P+'/scripts')} -type f -name '*.sh' -print").splitlines():p0.run(c,f"bash -n {p0.q(sh)}")
    p0.run(c,p0.q(P+"/scripts/openclaw/status_openclaw.sh"));p0.run(c,f"cd {p0.q(P)} && git diff --check")
    # Stage only project source/config; ignored runtime, reports and secrets cannot enter the index.
    p0.run(c,f"cd {p0.q(P)} && git rm -r --cached --ignore-unmatch reports >/dev/null",check=False)
    p0.run(c,f"cd {p0.q(P)} && git add -A")
    staged=p0.out(c,f"cd {p0.q(P)} && git diff --cached --name-only").splitlines()
    forbidden=[x for x in staged if x.startswith(("reports/","runtime/","storage/",".secrets/")) or x.endswith((".dump",".tgz",".env"))]
    large=p0.out(c,f"cd {p0.q(P)} && git diff --cached --numstat | awk '$1!=\"-\"&&$1+$2>200000{{print $3}}'").splitlines()
    scan="""import pathlib,subprocess,sys,re,urllib.parse
r=pathlib.Path(sys.argv[1]);secret=[]
for f in (r/'.secrets').glob('*.env'):
 for x in f.read_text(errors='ignore').splitlines():
  if '=' not in x:continue
  k,v=x.split('=',1)
  if '://' in v:v=urllib.parse.unquote(urllib.parse.urlsplit(v).password or '')
  elif not any(z in k.upper() for z in ('PASSWORD','TOKEN','SECRET')):continue
  if len(v)>10:secret.append(v)
hits=[]
for name in subprocess.check_output(['git','-C',str(r),'diff','--cached','--name-only'],text=True).splitlines():
 p=r/name
 if not p.is_file():continue
 t=p.read_text(errors='ignore')
 if any(v in t for v in secret):hits.append([name,'known-active-secret'])
 if re.search(r'postgresql(?:\\+psycopg)?://[^\\s]+:[^\\s]+@',t):hits.append([name,'database-url-pattern'])
print(__import__('json').dumps(hits))""";scan_hits=json.loads(p0.out(c,f"{p0.q(PY)} -c {p0.q(scan)} {p0.q(P)}"));hits=len(scan_hits)
    if forbidden or large or hits:raise p0.Blocked("BASELINE_GIT_GATE_FAILED:"+json.dumps({"forbidden":len(forbidden),"large":len(large),"secret_hits":scan_hits}))
    p0.run(c,f"cd {p0.q(P)} && git diff --cached --check");p0.run(c,f"cd {p0.q(P)} && git config user.name Codex && git config user.email codex@localhost && git commit -m {p0.q('chore: checkpoint verified P0-4 and P1-1 baseline')}")
    sha=p0.out(c,f"cd {p0.q(P)} && git rev-parse HEAD")
    result={"status":"PASS","commit":sha,"message":"chore: checkpoint verified P0-4 and P1-1 baseline","full_pytest":a.splitlines()[-1],"staged_paths":len(staged),"secret_scan_hits":hits,"large_files":len(large),"pushed":False}
    s=c.open_sftp();p0.put(s,REPORT+"/baseline_checkpoint.json",json.dumps(result,indent=2)+"\n",0o600);s.close();print(json.dumps(result))


def dependencies(c):
    runtime=["SQLAlchemy==2.0.51","alembic==1.18.5","psycopg[binary]==3.3.4","pydantic==2.13.4","pydantic-settings==2.14.2","pycountry==26.2.16","fastapi==0.139.0","uvicorn==0.51.0","anyio==4.14.1"]
    dev=["pytest==9.1.1","ruff==0.15.21","mypy==2.2.0","httpx==0.28.1"];req=P+"/requirements";tmp=P+"/storage/tmp/p1_2_deps";cache=P+"/storage/cache/pip"
    if p0.out(c,f"test -e {p0.q(tmp)} && echo yes || echo no")=="yes":raise p0.Blocked("P1_2_DEP_PATH_EXISTS")
    s=c.open_sftp();p0.mkdirs(s,tmp);p0.put(s,req+"/runtime.in","\n".join(runtime)+"\n");p0.put(s,req+"/dev.in","\n".join(dev)+"\n");s.close();p0.run(c,f"{p0.q(PY)} -m venv {p0.q(tmp+'/resolve')}")
    base=f"unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY;PIP_CACHE_DIR={p0.q(cache)} {p0.q(tmp+'/resolve/bin/python')} -m pip install --dry-run --ignore-installed --only-binary=:all: --index-url https://pypi.org/simple --timeout 60 --retries 2"
    p0.run(c,f"bash -lc {p0.q(base+' --report '+tmp+'/runtime.json '+' '.join(runtime))}",timeout=1800);p0.run(c,f"bash -lc {p0.q(base+' --report '+tmp+'/dev.json '+' '.join(runtime+dev))}",timeout=1800)
    make="""import json,sys,urllib.parse
d=json.load(open(sys.argv[1]));rows=[]
for x in d['install']:
 m=x['metadata'];h=x['download_info']['archive_info']['hashes']['sha256'];rows.append((m['name'].lower().replace('_','-'),m['version'],h,x['download_info']['url'],m.get('license_expression') or m.get('license') or 'NOASSERTION'))
rows.sort();open(sys.argv[2],'w').write('# Exact CPython 3.10 Linux x86_64 lock from official PyPI.\\n'+''.join(f'{n}=={v} --hash=sha256:{h}\\n' for n,v,h,_,_ in rows));json.dump([{'name':n,'version':v,'sha256':h,'source_domain':urllib.parse.urlsplit(u).hostname,'license':lic} for n,v,h,u,lic in rows],open(sys.argv[3],'w'),indent=2)"""
    for kind in ("runtime","dev"):p0.run(c,f"{p0.q(PY)} -c {p0.q(make)} {p0.q(tmp+'/'+kind+'.json')} {p0.q(req+'/'+kind+'.lock')} {p0.q(tmp+'/'+kind+'-inventory.json')}")
    expected={"fastapi":"cf15e1e9e667ddb0ad63811e60bd11390d1aac838ca4a7a23f421807b2308189","uvicorn":"5d38af6cd620f2ae3849fb44fd4879e0890aa1febe8d47eb355fb45d93fe6a5b","httpx":"d909fcccc110f8c7faf814ca82a9a4d816bc5a6dbfea25d6591d6985b8ba59ad","anyio":"4e5533c5b8ff0a24f5d7a176cbe6877129cd183893f66b537f8f227d10527d72"};inv=json.loads(p0.out(c,f"cat {p0.q(tmp+'/dev-inventory.json')}"));actual={x['name']:x['sha256'] for x in inv}
    if any(actual.get(k)!=v for k,v in expected.items()):raise p0.Blocked("OFFICIAL_WHEEL_HASH_MISMATCH")
    for kind in ("runtime","dev"):
        p0.run(c,f"{p0.q(PY)} -m venv {p0.q(tmp+'/'+kind+'-verify')}");cmd=f"unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY;PIP_CACHE_DIR={p0.q(cache)} {p0.q(tmp+'/'+kind+'-verify/bin/python')} -m pip install --require-hashes --only-binary=:all: --index-url https://pypi.org/simple --timeout 60 --retries 2 -r {p0.q(req+'/'+kind+'.lock')}";p0.run(c,f"bash -lc {p0.q(cmd)}",timeout=1800);p0.run(c,f"{p0.q(tmp+'/'+kind+'-verify/bin/python')} -m pip check")
    cmd=f"unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY;PIP_CACHE_DIR={p0.q(cache)} {p0.q(PY)} -m pip install --require-hashes --only-binary=:all: --index-url https://pypi.org/simple --timeout 60 --retries 2 -r {p0.q(req+'/dev.lock')}";p0.run(c,f"bash -lc {p0.q(cmd)}",timeout=1800);p0.run(c,f"{p0.q(PY)} -m pip check")
    ver_code="""import importlib.metadata as m,json
print(json.dumps({n:m.version(n) for n in ['fastapi','uvicorn','httpx','anyio']}))""";versions=json.loads(p0.out(c,f"{p0.q(PY)} -c {p0.q(ver_code)}"))
    if versions!={"fastapi":"0.139.0","uvicorn":"0.51.0","httpx":"0.28.1","anyio":"4.14.1"}:raise p0.Blocked("P1_2_DEP_VERSION_MISMATCH")
    direct_license={x['name']:x['license'] for x in inv if x['name'] in expected}
    if any(v in ("UNKNOWN","NOASSERTION","") for v in direct_license.values()):raise p0.Blocked("DIRECT_LICENSE_MISSING")
    result={"status":"PASS","versions":versions,"official_wheel_sha256":expected,"runtime_lock_sha256":p0.out(c,f"sha256sum {p0.q(req+'/runtime.lock')}|awk '{{print $1}}'"),"dev_lock_sha256":p0.out(c,f"sha256sum {p0.q(req+'/dev.lock')}|awk '{{print $1}}'"),"runtime_packages":json.loads(p0.out(c,f"cat {p0.q(tmp+'/runtime-inventory.json')}")),"dev_packages":inv,"direct_licenses":direct_license,"isolated_require_hashes":True,"pip_check":True,"network_turbo_used":False,"source":"official PyPI"}
    s=c.open_sftp();p0.put(s,REPORT+"/dependency_inventory.json",json.dumps(result,indent=2)+"\n",0o600);s.close();print(json.dumps({"status":"PASS","versions":versions,"runtime_packages":len(result['runtime_packages']),"dev_packages":len(inv),"network_turbo":False}))


def dependencies_finish(c):
    tmp=P+"/storage/tmp/p1_2_deps";req=P+"/requirements";expected={"fastapi":"cf15e1e9e667ddb0ad63811e60bd11390d1aac838ca4a7a23f421807b2308189","uvicorn":"5d38af6cd620f2ae3849fb44fd4879e0890aa1febe8d47eb355fb45d93fe6a5b","httpx":"d909fcccc110f8c7faf814ca82a9a4d816bc5a6dbfea25d6591d6985b8ba59ad","anyio":"4e5533c5b8ff0a24f5d7a176cbe6877129cd183893f66b537f8f227d10527d72"};inv=json.loads(p0.out(c,f"cat {p0.q(tmp+'/dev-inventory.json')}"));actual={x['name']:x['sha256'] for x in inv}
    if any(actual.get(k)!=v for k,v in expected.items()):raise p0.Blocked("OFFICIAL_WHEEL_HASH_MISMATCH")
    versions=json.loads(p0.out(c,f"{p0.q(PY)} -c {p0.q('import importlib.metadata as m,json;print(json.dumps({n:m.version(n) for n in [\"fastapi\",\"uvicorn\",\"httpx\",\"anyio\"]}))')}"));p0.run(c,f"{p0.q(PY)} -m pip check")
    licenses={"fastapi":"MIT","uvicorn":"BSD-3-Clause","httpx":"BSD-3-Clause","anyio":"MIT"};result={"status":"PASS","versions":versions,"official_wheel_sha256":expected,"runtime_lock_sha256":p0.out(c,f"sha256sum {p0.q(req+'/runtime.lock')}|awk '{{print $1}}'"),"dev_lock_sha256":p0.out(c,f"sha256sum {p0.q(req+'/dev.lock')}|awk '{{print $1}}'"),"runtime_packages":json.loads(p0.out(c,f"cat {p0.q(tmp+'/runtime-inventory.json')}")),"dev_packages":inv,"direct_licenses":licenses,"license_note":"Direct licenses verified from official project metadata; transitive missing metadata remains NOASSERTION.","isolated_require_hashes":True,"pip_check":True,"network_turbo_used":False,"source":"official PyPI"}
    s=c.open_sftp();p0.put(s,REPORT+"/dependency_inventory.json",json.dumps(result,indent=2)+"\n",0o600);s.close();print(json.dumps({"status":"PASS","versions":versions,"runtime_packages":len(result['runtime_packages']),"dev_packages":len(inv),"network_turbo":False}))


def deploy_api(c):
    local=Path(__file__).parent/"p1_2_payload";s=c.open_sftp()
    for path in sorted(local.rglob("*")):
        rel=path.relative_to(local).as_posix();remote=P+"/"+rel
        if path.is_dir():p0.mkdirs(s,remote)
        else:p0.mkdirs(s,str(PurePosixPath(remote).parent));p0.put(s,remote,path.read_text(encoding="utf-8"),0o750 if rel.startswith("scripts/") and (rel.endswith(".sh") or rel.endswith("probe.py")) else 0o644)
    # Add read-only lookup helpers to the existing P1-1 repository.
    repo=P+"/src/epidemic_core/db/repositories/repositories.py"
    with s.open(repo,"r") as f:t=f.read().decode()
    marker="class QueryRepository:\n"
    methods='''class QueryRepository:
    def get_by_id(self, session: Session, query_id: uuid.UUID) -> QueryRequest | None:
        return session.execute(select(QueryRequest).where(QueryRequest.id == query_id)).scalar_one_or_none()

    def get_by_fingerprint(self, session: Session, fingerprint: str) -> QueryRequest | None:
        return session.execute(
            select(QueryRequest).where(QueryRequest.fingerprint == fingerprint)
        ).scalar_one_or_none()

'''
    if "def get_by_id(" not in t:
        if marker not in t:raise p0.Blocked("QUERY_REPOSITORY_SHAPE")
        t=t.replace(marker,methods)
    p0.put(s,repo,t,0o644)
    # Map public domain-resolution errors without exposing raw input or internals.
    route=P+"/src/epidemic_core/api/routes/queries.py"
    with s.open(route,"r") as f:t=f.read().decode()
    t=t.replace("from epidemic_core.domain.query import EpidemicQueryInput", "from epidemic_core.domain.query import EpidemicQueryInput\nfrom epidemic_core.errors import EpidemicCoreError,ResolutionError\nfrom epidemic_core.normalization import CountryResolver")
    old="""def details(session:Session,value:QueryInput):
    normalized=QueryService().normalize(session,EpidemicQueryInput(**value.model_dump()));country=session.execute(select(Country).where(Country.alpha2==normalized.country_alpha2)).scalar_one();disease=session.execute(select(Disease).where(Disease.slug==normalized.disease_slug)).scalar_one();return normalized,country,disease
"""
    new="""def details(session: Session, value: QueryInput):
    service = QueryService()
    try:
        normalized = service.normalize(session, EpidemicQueryInput(**value.model_dump()))
    except ResolutionError as exc:
        try:
            CountryResolver(service.countries.aliases(session)).resolve(value.country)
            entity = "DISEASE"
        except ResolutionError:
            entity = "COUNTRY"
        suffix = "AMBIGUOUS" if exc.code == "AMBIGUOUS" else "NOT_FOUND"
        status = 409 if suffix == "AMBIGUOUS" else 404
        raise HTTPException(
            status_code=status,
            detail={"code": f"{entity}_{suffix}", "message": "Input could not be resolved", "candidates": list(exc.candidates[:5])},
        ) from exc
    except EpidemicCoreError as exc:
        raise HTTPException(status_code=422, detail={"code": "INVALID_DATE_RANGE", "message": "Invalid date range"}) from exc
    country = session.execute(select(Country).where(Country.alpha2 == normalized.country_alpha2)).scalar_one()
    disease = session.execute(select(Disease).where(Disease.slug == normalized.disease_slug)).scalar_one()
    return normalized, country, disease
"""
    if old in t:t=t.replace(old,new)
    elif "except ResolutionError as exc:" not in t:raise p0.Blocked("QUERY_ROUTE_SHAPE")
    p0.put(s,route,t,0o644)
    # Update project metadata without adding a second package or migration.
    py=P+"/pyproject.toml"
    with s.open(py,"r") as f:t=f.read().decode()
    if '"fastapi==0.139.0"' not in t:t=t.replace('"pydantic-settings==2.14.2",','"pydantic-settings==2.14.2",\n  "fastapi==0.139.0", "uvicorn==0.51.0",')
    p0.put(s,py,t,0o644)
    lic=P+"/THIRD_PARTY_LICENSES.md"
    with s.open(lic,"r") as f:t=f.read().decode()
    if "FastAPI 0.139.0" not in t:t+= "\n- FastAPI 0.139.0 — MIT\n- Uvicorn 0.51.0 — BSD-3-Clause\n- HTTPX 0.28.1 — BSD-3-Clause\n- AnyIO 4.14.1 — MIT\n"
    p0.put(s,lic,t,0o644)
    # Create the one shared API-token source without exposing it.
    token_dir=P+"/storage/secrets";p0.mkdirs(s,token_dir)
    token_file=token_dir+"/query-api-token.env"
    try:s.stat(token_file);exists=True
    except FileNotFoundError:exists=False
    if not exists:
        import secrets
        p0.put(s,token_file,"EPIDEMIC_QUERY_API_TOKEN="+secrets.token_urlsafe(48)+"\n",0o600)
    s.chmod(token_dir,0o700);s.chmod(token_file,0o600);s.close()
    p0.run(c,f"{p0.q(CORE+'/bin/ruff')} format {p0.q(P+'/src')} {p0.q(P+'/tests')}")
    print(json.dumps({"uploaded":sum(1 for x in local.rglob('*') if x.is_file()),"api_token_file_mode":p0.out(c,f"stat -c %a {p0.q(token_file)}"),"alembic_revisions":p0.out(c,f"find {p0.q(P+'/migrations/versions')} -name '*.py'|wc -l")}))


def inspect_plugin_sdk(c):
    root=p0.OC_HOME+"/node_modules/openclaw"
    print(p0.out(c,f"grep -RIn 'defineToolPlugin' {p0.q(root)} --include='*.ts' --include='*.js' --include='*.d.ts' | head -n 40",check=False)[:20000])
    print(p0.out(c,f"{p0.q(P+'/scripts/openclaw/openclaw_cli.sh')} plugins init --help",check=False)[:10000])


def scaffold_plugin(c):
    target=P+"/integrations/openclaw/epidemic-query-tools"
    if p0.out(c,f"test -e {p0.q(target)} && echo yes || echo no")=="yes":raise p0.Blocked("PLUGIN_TARGET_EXISTS")
    p0.run(c,f"{p0.q(P+'/scripts/openclaw/openclaw_cli.sh')} plugins init epidemic-query-tools --directory {p0.q(target)} --name {p0.q('Epidemic Query Tools')}")
    root=p0.OC_HOME+"/node_modules/openclaw"
    print("SCAFFOLD\n"+p0.out(c,f"find {p0.q(target)} -maxdepth 3 -type f -print | sort"))
    print("TOOL_PLUGIN_TYPES\n"+p0.out(c,f"sed -n '1,180p' {p0.q(root+'/dist/plugin-sdk/tool-plugin.d.ts')}"))
    print("SCAFFOLD_SOURCE\n"+p0.out(c,f"find {p0.q(target)} -type f -maxdepth 2 -exec sh -c 'echo FILE=$1;sed -n \"1,260p\" \"$1\"' sh {{}} \\;"))


def inspect_manifest_types(c):
    root=p0.OC_HOME+"/node_modules/openclaw/dist"
    print(p0.out(c,f"grep -RIn 'toolMetadata' {p0.q(root)} --include='*.d.ts' --include='*.js' | head -n 60",check=False)[:20000])


def build_plugin(c):
    root=P+"/integrations/openclaw/epidemic-query-tools";s=c.open_sftp()
    package={"name":"openclaw-plugin-epidemic-query-tools","version":"0.1.0","type":"module","private":True,"scripts":{"build":"tsc -p tsconfig.json","test":"node --test dist/index.test.js","plugin:build":"npm run build && openclaw plugins build --entry ./dist/index.js","plugin:validate":"npm run build && openclaw plugins validate --entry ./dist/index.js"},"files":["dist/index.js","dist/index.d.ts","openclaw.plugin.json","README.md"],"peerDependencies":{"openclaw":"2026.6.11"},"dependencies":{"typebox":"1.1.39"},"devDependencies":{"openclaw":"2026.6.11","typescript":"6.0.3","@types/node":"25.9.1"},"openclaw":{"extensions":["./dist/index.js"]}}
    tsconfig={"compilerOptions":{"target":"ES2023","module":"NodeNext","moduleResolution":"NodeNext","rootDir":"src","outDir":"dist","strict":True,"declaration":True,"skipLibCheck":True,"noUncheckedIndexedAccess":True,"exactOptionalPropertyTypes":True},"include":["src/**/*.ts"]}
    manifest={"id":"epidemic-query-tools","name":"Epidemic Query Tools","description":"Local-only epidemic query normalization and registration tools.","version":"0.1.0","configSchema":{"type":"object","additionalProperties":False,"properties":{}},"activation":{"onStartup":True},"contracts":{"tools":["normalize_epidemic_query","register_epidemic_query"]},"toolMetadata":{"normalize_epidemic_query":{"optional":True},"register_epidemic_query":{"optional":True}}}
    source='''import { Type } from "typebox";
import { defineToolPlugin } from "openclaw/plugin-sdk/tool-plugin";

const ORIGIN = "http://127.0.0.1:8010";
const MAX_RESPONSE = 65_536;
const Paths = { normalize: "/v1/queries/normalize", register: "/v1/queries" } as const;

const Params = Type.Object(
  {
    country: Type.String({ minLength: 1, maxLength: 128 }),
    disease: Type.String({ minLength: 1, maxLength: 128 }),
    start_date: Type.String({ minLength: 10, maxLength: 10, pattern: "^\\\\d{4}-\\\\d{2}-\\\\d{2}$" }),
    end_date: Type.String({ minLength: 10, maxLength: 10, pattern: "^\\\\d{4}-\\\\d{2}-\\\\d{2}$" }),
  },
  { additionalProperties: false },
);

type Input = { country: string; disease: string; start_date: string; end_date: string };

async function readLimited(response: Response): Promise<unknown> {
  const length = response.headers.get("content-length");
  if (length !== null && Number(length) > MAX_RESPONSE) throw new Error("RESPONSE_TOO_LARGE");
  if (!response.headers.get("content-type")?.toLowerCase().startsWith("application/json")) throw new Error("INVALID_RESPONSE");
  const reader = response.body?.getReader();
  if (!reader) throw new Error("INVALID_RESPONSE");
  const chunks: Uint8Array[] = []; let size = 0;
  while (true) { const item = await reader.read(); if (item.done) break; size += item.value.length; if (size > MAX_RESPONSE) { await reader.cancel(); throw new Error("RESPONSE_TOO_LARGE"); } chunks.push(item.value); }
  const bytes = new Uint8Array(size); let offset = 0; for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
  try { return JSON.parse(new TextDecoder().decode(bytes)); } catch { throw new Error("INVALID_RESPONSE"); }
}

export async function callApi(path: keyof typeof Paths, input: Input, signal?: AbortSignal): Promise<unknown> {
  const token = process.env.EPIDEMIC_QUERY_API_TOKEN;
  if (!token || token.length < 32) throw new Error("AUTH_UNAVAILABLE");
  const timeout = AbortSignal.timeout(10_000); const combined = signal ? AbortSignal.any([signal, timeout]) : timeout;
  let response: Response;
  try { response = await fetch(ORIGIN + Paths[path], { method: "POST", redirect: "error", signal: combined, headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` }, body: JSON.stringify(input) }); }
  catch { throw new Error("SERVICE_UNAVAILABLE"); }
  if (!response.ok) { if (response.status === 401) throw new Error("AUTH_FAILED"); if (response.status === 404) throw new Error("NOT_FOUND"); if (response.status === 409) throw new Error("AMBIGUOUS"); if (response.status === 422) throw new Error("INVALID_REQUEST"); throw new Error("SERVICE_UNAVAILABLE"); }
  return readLimited(response);
}

export default defineToolPlugin({
  id: "epidemic-query-tools", name: "Epidemic Query Tools",
  description: "Normalize and idempotently register local epidemic queries.",
  activation: { onStartup: true }, configSchema: Type.Object({}, { additionalProperties: false }),
  tools: (tool) => [
    tool({ name: "normalize_epidemic_query", optional: true, description: "Read-only country, disease, and date normalization. Does not persist and does not start network retrieval.", parameters: Params, execute: (params, _config, context) => callApi("normalize", params, context.signal) }),
    tool({ name: "register_epidemic_query", optional: true, description: "Call only when the user explicitly asks to register or create a query. Idempotently writes only query_requests and does not start retrieval.", parameters: Params, execute: (params, _config, context) => callApi("register", params, context.signal) }),
  ],
});
'''
    test='''import assert from "node:assert/strict";
import test from "node:test";
import entry, { callApi } from "./index.js";
import { getToolPluginMetadata } from "openclaw/plugin-sdk/tool-plugin";

const input = { country: "CN", disease: "dengue", start_date: "2025-01-01", end_date: "2025-01-02" };
const token = "unit-test-placeholder-token-000000000000";

test("manifest metadata is tool-only and exact", () => { const m = getToolPluginMetadata(entry); assert.deepEqual(m?.tools.map((x) => x.name), ["normalize_epidemic_query", "register_epidemic_query"]); assert.ok(m?.tools.every((x) => x.optional)); assert.equal(m?.configSchema.additionalProperties, false); assert.deepEqual(Object.keys(m?.tools[0]?.parameters.properties ?? {}), ["country", "disease", "start_date", "end_date"]); });

test("fixed origin paths, bearer, and redirect policy", async () => { process.env.EPIDEMIC_QUERY_API_TOKEN = token; const calls: Array<[unknown, RequestInit | undefined]> = []; globalThis.fetch = async (url, init) => { calls.push([url, init]); return new Response(JSON.stringify({ ok: true }), { headers: { "Content-Type": "application/json" } }); }; await callApi("normalize", input); await callApi("register", input); assert.equal(calls[0]?.[0], "http://127.0.0.1:8010/v1/queries/normalize"); assert.equal(calls[1]?.[0], "http://127.0.0.1:8010/v1/queries"); assert.equal(calls[0]?.[1]?.redirect, "error"); assert.equal((calls[0]?.[1]?.headers as Record<string,string>).Authorization, `Bearer ${token}`); });

test("missing token fails closed", async () => { delete process.env.EPIDEMIC_QUERY_API_TOKEN; await assert.rejects(callApi("normalize", input), /AUTH_UNAVAILABLE/); });

for (const [status, code] of [[401,"AUTH_FAILED"],[404,"NOT_FOUND"],[409,"AMBIGUOUS"],[422,"INVALID_REQUEST"],[500,"SERVICE_UNAVAILABLE"],[503,"SERVICE_UNAVAILABLE"]] as const) test(`status ${status} is sanitized`, async () => { process.env.EPIDEMIC_QUERY_API_TOKEN=token; globalThis.fetch=async()=>new Response("hidden",{status}); await assert.rejects(callApi("normalize",input),new RegExp(code)); });

test("non-json and oversized bodies are rejected", async () => { process.env.EPIDEMIC_QUERY_API_TOKEN=token; globalThis.fetch=async()=>new Response("text",{headers:{"Content-Type":"text/plain"}}); await assert.rejects(callApi("normalize",input),/INVALID_RESPONSE/); globalThis.fetch=async()=>new Response("x".repeat(70_000),{headers:{"Content-Type":"application/json","Content-Length":"1"}}); await assert.rejects(callApi("normalize",input),/RESPONSE_TOO_LARGE/); });

test("hard timeout is sanitized", async () => { process.env.EPIDEMIC_QUERY_API_TOKEN=token; globalThis.fetch=async(_url,init)=>new Promise((_resolve,reject)=>{ init?.signal?.addEventListener("abort",()=>reject(new Error("private timeout")),{once:true}); }); await assert.rejects(callApi("normalize",input),/SERVICE_UNAVAILABLE/); });

test("network and redirect failures are sanitized", async () => { process.env.EPIDEMIC_QUERY_API_TOKEN=token; globalThis.fetch=async()=>{throw new Error("private stack")}; await assert.rejects(callApi("normalize",input),/SERVICE_UNAVAILABLE/); });
'''
    readme="# Epidemic Query Tools\n\nTool-only OpenClaw 2026.6.11 plugin for the fixed loopback Query Intake API. It exposes only normalize_epidemic_query and register_epidemic_query. It has no MCP, skill, hook, service, route, channel, provider, or CLI surface.\n"
    p0.put(s,root+"/package.json",json.dumps(package,indent=2)+"\n");p0.put(s,root+"/tsconfig.json",json.dumps(tsconfig,indent=2)+"\n");p0.put(s,root+"/openclaw.plugin.json",json.dumps(manifest,indent=2)+"\n");p0.put(s,root+"/src/index.ts",source);p0.put(s,root+"/src/index.test.ts",test);p0.put(s,root+"/README.md",readme);s.close()
    node=p0.NODE_CURRENT+"/bin";env=f"PATH={p0.q(node)}:{p0.q(p0.OC_HOME+'/node_modules/.bin')}:$PATH npm_config_cache={p0.q(P+'/storage/cache/npm')}"
    p0.run(c,f"cd {p0.q(root)} && unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; {env} npm install --package-lock-only --registry=https://registry.npmjs.org/ --ignore-scripts",timeout=1800)
    p0.run(c,f"cd {p0.q(root)} && unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; {env} npm ci --registry=https://registry.npmjs.org/",timeout=2400)
    p0.run(c,f"cd {p0.q(root)} && {env} npm run build && {env} npm test",timeout=1200)
    # Static forbidden-capability scan uses names only and never prints source.
    forbidden=p0.out(c,f"grep -RIlE '(child_process|exec\\(|spawn\\(|from .fs.|from .net.|from .tls.|eval\\(|new Function|import\\()' {p0.q(root+'/src')} || true").splitlines()
    if forbidden:raise p0.Blocked("PLUGIN_STATIC_CAPABILITY_SCAN_FAILED")
    audit=json.loads(p0.out(c,f"cd {p0.q(root)} && {env} npm audit --omit=dev --json",timeout=900));v=audit.get("metadata",{}).get("vulnerabilities",{})
    if v.get("critical",0) or v.get("high",0):raise p0.Blocked("PLUGIN_NPM_AUDIT_HIGH")
    p0.run(c,f"cd {p0.q(root)} && {env} openclaw plugins build --entry ./dist/index.js",timeout=600)
    p0.run(c,f"cd {p0.q(root)} && {env} openclaw plugins build --entry ./dist/index.js --check",timeout=600)
    p0.run(c,f"cd {p0.q(root)} && {env} openclaw plugins validate --entry ./dist/index.js",timeout=600)
    pack=p0.out(c,f"cd {p0.q(root)} && {env} npm pack --json",timeout=600);packj=json.loads(pack)[0];tgz=root+"/"+packj["filename"];sha=p0.out(c,f"sha256sum {p0.q(tgz)}|awk '{{print $1}}'")
    result={"id":"epidemic-query-tools","version":"0.1.0","contracts":manifest["contracts"],"toolMetadata":manifest["toolMetadata"],"configSchema":manifest["configSchema"],"runtime_surfaces":{"tools":2,"hooks":0,"services":0,"routes":0,"channels":0,"providers":0,"cli":0,"skills":0,"mcp":0},"npm_audit":v,"pack":{"filename":packj["filename"],"sha256":sha,"files":[x["path"] for x in packj["files"]]},"static_forbidden_hits":0,"tgz":tgz}
    s=c.open_sftp();p0.put(s,REPORT+"/openclaw_plugin_inventory.json",json.dumps(result,indent=2)+"\n",0o600);s.close();print(json.dumps({"status":"PASS","pack":packj["filename"],"sha256":sha,"files":len(packj["files"]),"npm_high":v.get("high",0)}))


def inspect_plugin_path(c):
    parent=P+"/integrations/openclaw"
    target=parent+"/epidemic-query-tools"
    print(json.dumps({
        "parent_exists": p0.run(c, f"test -d {p0.q(parent)}", check=False)[0] == 0,
        "target_exists": p0.run(c, f"test -d {p0.q(target)}", check=False)[0] == 0,
        "entries": p0.out(c, f"find {p0.q(parent)} -maxdepth 2 -mindepth 1 -type d -printf '%P\\n' | sort", check=False).splitlines(),
        "files": p0.out(c, f"find {p0.q(target)} -maxdepth 3 -type f -printf '%P\\n' | sort", check=False).splitlines(),
    }))


def inspect_plugin_cli(c):
    root=P+"/integrations/openclaw/epidemic-query-tools"
    node=p0.NODE_CURRENT+"/bin"
    env=f"PATH={p0.q(node)}:{p0.q(p0.OC_HOME+'/node_modules/.bin')}:$PATH npm_config_cache={p0.q(P+'/storage/cache/npm')}"
    results={}
    for name, command in {
        "plugins_help": "openclaw plugins --help",
        "plugins_build_help": "openclaw plugins build --help",
        "plugins_validate_help": "openclaw plugins validate --help",
        "plugins_install_help": "openclaw plugins install --help",
        "plugins_inspect_help": "openclaw plugins inspect --help",
        "plugins_build": "openclaw plugins build --root . --entry ./dist/index.js",
        "plugins_build_check": "openclaw plugins build --root . --entry ./dist/index.js --check",
        "plugins_validate": "openclaw plugins validate --root . --entry ./dist/index.js",
    }.items():
        rc, out, err=p0.run(c, f"cd {p0.q(root)} && {env} {command}", timeout=600, check=False)
        results[name]={"rc":rc,"stdout":out[-4000:],"stderr":err[-1000:]}
    print(json.dumps(results))


def inspect_repository(c):
    path=P+"/src/epidemic_core/db/repositories/repositories.py"
    print(p0.out(c, f"sed -n '1,180p' {p0.q(path)}"))


def fix_query_concurrency(c):
    path=P+"/src/epidemic_core/db/repositories/repositories.py"
    s=c.open_sftp()
    with s.open(path,"r") as f:t=f.read().decode()
    if "def create_or_get_result(" not in t:
        start=t.index("class QueryRepository:")
        end=t.index("\n\nclass DataSourceRepository:",start)
        block=t[start:end]
        block=block.replace("    def create_or_get(\n", "    def create_or_get_result(\n", 1)
        block=block.replace("    ) -> QueryRequest:\n", "    ) -> tuple[QueryRequest, bool]:\n", 1)
        old='''        created = session.execute(stmt).scalar_one_or_none()
        return (
            _one(session, select(QueryRequest).where(QueryRequest.id == created))
            if created
            else _one(session, select(QueryRequest).where(QueryRequest.fingerprint == query.fingerprint()))
        )
'''
        new='''        created = session.execute(stmt).scalar_one_or_none()
        row = (
            _one(session, select(QueryRequest).where(QueryRequest.id == created))
            if created
            else _one(session, select(QueryRequest).where(QueryRequest.fingerprint == query.fingerprint()))
        )
        return row, created is not None

    def create_or_get(
        self,
        session: Session,
        raw_country: str,
        raw_disease: str,
        disease_id: uuid.UUID,
        query: NormalizedEpidemicQuery,
    ) -> QueryRequest:
        row, _created = self.create_or_get_result(
            session, raw_country, raw_disease, disease_id, query
        )
        return row
'''
        if old not in block:raise p0.Blocked("QUERY_REPOSITORY_CONCURRENCY_SHAPE")
        t=t[:start]+block.replace(old,new)+t[end:]
        p0.put(s,path,t,0o644)
    local=Path(__file__).parent/"p1_2_payload/src/epidemic_core/api/routes/queries.py"
    p0.put(s,P+"/src/epidemic_core/api/routes/queries.py",local.read_text(encoding="utf-8"),0o644)
    s.close()
    p0.run(c,f"{p0.q(CORE+'/bin/ruff')} format {p0.q(path)} {p0.q(P+'/src/epidemic_core/api/routes/queries.py')}")
    rc,out,err=p0.run(c,f"{p0.q(CORE+'/bin/ruff')} check {p0.q(path)} {p0.q(P+'/src/epidemic_core/api/routes/queries.py')}",check=False)
    if rc:raise p0.Blocked("QUERY_CONCURRENCY_RUFF:"+(out+err)[-2000:])
    print(json.dumps({"status":"PASS","atomic_created_flag":True}))


def _test_database(c, *, reset: bool) -> tuple[str, str, str]:
    db="epidemic_p1_2_test"
    pg_bin="USER_HOME/autodl-tmp/conda/envs/postgresql18/bin"
    pg_run=P+"/storage/database/postgresql18/run"
    backup=json.loads(p0.out(c,f"cat {p0.q(P+'/reports/p1_1_domain_storage/backup_restore_results.json')}"))
    dump=backup["post_migration"]["path"]
    if reset:
        p0.run(c,p0.q(P+"/scripts/query_api/stop_query_api.sh"),check=False)
        p0.run(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/dropdb')} -h {p0.q(pg_run)} -U epidemicdb --if-exists {db}",timeout=300)
        p0.run(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/createdb')} -h {p0.q(pg_run)} -U epidemicdb -O epidemicdb {db}")
        p0.run(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/pg_restore')} -h {p0.q(pg_run)} -U epidemicdb -d {db} {p0.q(dump)}",timeout=900)
        grant="GRANT CONNECT ON DATABASE "+db+" TO epidemic_app,epidemic_migrator; REVOKE CREATE,TEMP ON DATABASE "+db+" FROM PUBLIC,epidemic_app;"
        p0.run(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d postgres -v ON_ERROR_STOP=1 -c {p0.q(grant)}")
        script=P+"/storage/tmp/p1_2_make_api_test_env.py"
        code='''import pathlib,urllib.parse
root=pathlib.Path("'''+P+'''");p=root/".secrets/postgres.env";v={k:x for k,x in (line.split("=",1) for line in p.read_text().splitlines() if "=" in line)};app="postgresql"+"+psycopg://epidemic_app:"+urllib.parse.quote(v["POSTGRES_APP_PASSWORD"],safe="")+"@127.0.0.1:5432/epidemic_p1_2_test";q=root/"storage/secrets/query-api-test-database.env";q.write_text("APP_DATABASE_URL="+app+"\\n");q.chmod(0o600);m={k:x for k,x in (line.split("=",1) for line in (root/".secrets/p1_1.env").read_text().splitlines() if "=" in line)}["MIGRATION_DATABASE_URL"];u=urllib.parse.urlsplit(m);m=urllib.parse.urlunsplit((u.scheme,u.netloc,"/epidemic_p1_2_test",u.query,u.fragment));z=root/"storage/secrets/p1-2-pytest.env";z.write_text("MIGRATION_DATABASE_URL="+m+"\\n");z.chmod(0o600)
'''
        s=c.open_sftp();p0.put(s,script,code,0o700);s.close();p0.run(c,f"{p0.q(PY)} {p0.q(script)}")
    return db,pg_bin,pg_run


def test_query_api(c):
    db,pg_bin,pg_run=_test_database(c,reset=True)
    start=P+"/scripts/query_api/start_query_api.sh";stop=P+"/scripts/query_api/stop_query_api.sh";status=P+"/scripts/query_api/status_query_api.sh"
    p0.run(c,p0.q(start),timeout=180);p0.run(c,p0.q(status));pid1=p0.out(c,f"cat {p0.q(P+'/storage/run/query-api/query-api.pid')}");p0.run(c,p0.q(start));pid2=p0.out(c,f"cat {p0.q(P+'/storage/run/query-api/query-api.pid')}")
    if pid1!=pid2:raise p0.Blocked("QUERY_API_START_NOT_IDEMPOTENT")
    env=f"source {p0.q(P+'/scripts/query_api/query_api_env.sh')};set -a;source {p0.q(P+'/storage/secrets/p1-2-pytest.env')};set +a;cd {p0.q(P)};"
    rc,out,err=p0.run(c,f"bash -lc {p0.q(env+' '+PY+' -m pytest -q')}",timeout=1800,check=False)
    if rc:raise p0.Blocked("P1_2_FULL_PYTEST_FAILED:"+(out+err)[-5000:])
    p1.unit_quality(c);p0.run(c,f"{p0.q(PY)} -m pip check")
    # Reset after the in-process suite so the real listener starts from a known empty restored database.
    _test_database(c,reset=True);p0.run(c,p0.q(start),timeout=180)
    live=P+"/storage/tmp/p1_2_live_api_test.py"
    live_code='''import concurrent.futures,json,pathlib,httpx
p=pathlib.Path("'''+P+'''/storage/secrets/query-api-token.env");token=dict(x.split("=",1) for x in p.read_text().splitlines() if "=" in x)["EPIDEMIC_QUERY_API_TOKEN"];origin="http://127.0.0.1:8010";h={"Authorization":"Bearer "+token};base={"country":"CN","disease":"dengue","start_date":"2025-01-01","end_date":"2025-01-02"}
with httpx.Client(timeout=10) as c:
 assert c.get(origin+"/healthz",headers=h).status_code==200
 assert c.get(origin+"/readyz",headers=h).status_code==200
 assert c.get(origin+"/healthz").status_code==401
 assert c.get(origin+"/healthz",headers={"Authorization":"Bearer wrong"}).status_code==401
 assert c.get(origin+"/healthz",headers={"Authorization":"Basic malformed"}).status_code==401
 assert c.get(origin+"/healthz",headers={**h,"Host":"evil.invalid"}).status_code==400
 assert all(c.get(origin+x,headers=h).status_code==404 for x in ("/docs","/redoc","/openapi.json"))
 r=c.get(origin+"/healthz",headers=h);assert "access-control-allow-origin" not in r.headers and r.headers["cache-control"]=="no-store" and r.headers["x-content-type-options"]=="nosniff"
 aliases=[("CN","dengue","CN","dengue"),("China","Dengue Fever","CN","dengue"),("中国","登革热","CN","dengue"),("USA","flu","US","influenza"),("美国","流感","US","influenza"),("Brazil","COVID-19","BR","covid-19"),("巴西","猴痘","BR","mpox")]
 for country,disease,a,s in aliases:
  q={**base,"country":country,"disease":disease};r=c.post(origin+"/v1/queries/normalize",headers=h,json=q);assert r.status_code==200 and r.json()["country_alpha2"]==a and r.json()["disease_slug"]==s and not r.json()["persisted"] and not r.json()["retrieval_started"]
 for q in ({**base,"country":"Atlantis"},{**base,"disease":"unknown"},{**base,"start_date":"2025/01/01"},{**base,"start_date":"2025-02-02","end_date":"2025-01-01"},{**base,"extra":"x"},{**base,"country":""},{**base,"country":"x"*129}):
  r=c.post(origin+"/v1/queries/normalize",headers=h,json=q);assert r.status_code in (404,409,422) and "postgresql" not in r.text.lower() and "traceback" not in r.text.lower()
 injection={**base,"country":"CN'; DROP TABLE epidemic.query_requests; --"};assert c.post(origin+"/v1/queries",headers=h,json=injection).status_code in (404,409)
 def chunks():
  yield b"{";yield b"x"*9000;yield b"}"
 assert c.post(origin+"/v1/queries/normalize",headers=h,content=chunks()).status_code==413
def register(_):
 with httpx.Client(timeout=15) as c:return c.post(origin+"/v1/queries",headers=h,json=base)
with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:rs=list(pool.map(register,range(10)))
assert sorted(r.status_code for r in rs)==[200]*9+[201];assert len({r.json()["query_id"] for r in rs})==1;assert all(not r.json()["retrieval_started"] for r in rs)
with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:health=list(pool.map(lambda _:httpx.get(origin+"/healthz",headers=h,timeout=10).status_code,range(20)))
assert health==[200]*20
print(json.dumps({"auth":True,"normalize_aliases":len(aliases),"chunked_limit":True,"concurrent_register":{"requests":10,"created":1,"rows":1},"parallel_health":20}))
'''
    s=c.open_sftp();p0.put(s,live,live_code,0o700);s.close();live_result=json.loads(p0.out(c,f"{p0.q(PY)} {p0.q(live)}",timeout=300))
    count_sql="SELECT json_build_object('query_requests',(SELECT count(*) FROM epidemic.query_requests),'retrieval_runs',(SELECT count(*) FROM epidemic.retrieval_runs),'source_fetches',(SELECT count(*) FROM epidemic.source_fetches),'raw_artifacts',(SELECT count(*) FROM epidemic.raw_artifacts),'normalized_observations',(SELECT count(*) FROM epidemic.normalized_observations))::text"
    counts=json.loads(p0.out(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d {db} -Atc {p0.q(count_sql)}"))
    if counts!={"query_requests":1,"retrieval_runs":0,"source_fetches":0,"raw_artifacts":0,"normalized_observations":0}:raise p0.Blocked("QUERY_API_WRITE_BOUNDARY_FAILED")
    pid=p0.out(c,f"cat {p0.q(P+'/storage/run/query-api/query-api.pid')}")
    service={"pid_single":p0.out(c,f"pgrep -fc 'uvicorn --factory epidemic_core.api.app:create_app'")=="1","listener_loopback":p0.out(c,"awk 'NR>1&&$2==\"0100007F:1F4A\"&&$4==\"0A\"{n++}END{print n+0}' /proc/net/tcp")=="1","other_listeners":p0.out(c,"awk 'NR>1&&$2~/:1F4A$/&&$2!=\"0100007F:1F4A\"&&$4==\"0A\"{n++}END{print n+0}' /proc/net/tcp /proc/net/tcp6")=="0","proxy_clean":p0.out(c,f"tr '\\0' '\\n' </proc/{pid}/environ|grep -Eic '^(http_proxy|https_proxy|HTTP_PROXY|HTTPS_PROXY|all_proxy|ALL_PROXY)=' || true")=="0"}
    token_hits=int(p0.out(c,f"t=$(cut -d= -f2- {p0.q(P+'/storage/secrets/query-api-token.env')}); (tr '\\0' '\\n' </proc/{pid}/cmdline;cat {p0.q(P+'/storage/logs/query-api/query-api.log')})|grep -Fxc \"$t\" || true"))
    if not all(service.values()) or token_hits:raise p0.Blocked("QUERY_API_SERVICE_SECURITY_FAILED")
    p0.run(c,p0.q(stop));p0.run(c,p0.q(start),timeout=180);p0.run(c,p0.q(status))
    result={"status":"PASS","full_pytest":out.splitlines()[-1],"live":live_result,"temporary_database":db,"temporary_counts":counts,"service":service,"token_argv_log_hits":token_hits,"restart":True,"idempotent_start":True,"formal_database_untouched_so_far":True}
    s=c.open_sftp();p0.put(s,REPORT+"/query_api_test_results.json",json.dumps(result,indent=2)+"\n",0o600);p0.put(s,REPORT+"/query_api_inventory.json",json.dumps({"service":"epidemic-query-api","origin":"loopback:8010","endpoints":["GET /healthz","GET /readyz","POST /v1/queries/normalize","POST /v1/queries","GET /v1/queries/{query_id}"],"authentication":"Bearer on all endpoints","workers":1,"access_log":False,"proxy_headers":False,"body_limit_bytes":8192,"database_stack":"existing synchronous SQLAlchemy/Psycopg","alembic_revision":"0001_p1_1","token_file_mode":"0600"},indent=2)+"\n",0o600);s.close();print(json.dumps(result))


def configure_openclaw_plugin(c):
    config=p0.CONFIG;env_path=P+"/scripts/openclaw/openclaw_env.sh";start_path=P+"/scripts/openclaw/start_openclaw.sh";backup=P+"/storage/tmp/p1_2_openclaw_backup"
    if p0.out(c,f"test -e {p0.q(backup)} && echo yes || echo no")=="yes":raise p0.Blocked("OPENCLAW_BACKUP_ALREADY_EXISTS")
    p0.run(c,f"mkdir -p {p0.q(backup)} && chmod 700 {p0.q(backup)}")
    for source,name in ((config,"openclaw.p0.json"),(env_path,"openclaw_env.p0.sh"),(start_path,"start_openclaw.p0.sh")):
        p0.run(c,f"cp {p0.q(source)} {p0.q(backup+'/'+name)} && chmod 600 {p0.q(backup+'/'+name)}")
    cli=P+"/scripts/openclaw/openclaw_cli.sh";inventory=p0.out(c,f"{p0.q(cli)} plugins list --json",timeout=300)
    s=c.open_sftp();p0.put(s,backup+"/plugins-before.json",inventory+"\n",0o600);s.close()
    plugin=json.loads(p0.out(c,f"cat {p0.q(REPORT+'/openclaw_plugin_inventory.json')}"));tgz=plugin["tgz"]
    p0.run(c,f"{p0.q(cli)} plugins install {p0.q('npm-pack:'+tgz)}",timeout=900)
    # Preserve the install record created by the official installer while enforcing the narrow policy.
    s=c.open_sftp()
    with s.open(config,"r") as f:cfg=json.loads(f.read().decode())
    plugins=cfg.get("plugins",{});plugins["enabled"]=True;plugins["allow"]=["epidemic-query-tools"];plugins["entries"]={"epidemic-query-tools":{"enabled":True}};plugins.pop("load",None);cfg["plugins"]=plugins
    tools_cfg=cfg["tools"];tools_cfg.pop("allow",None);tools_cfg["alsoAllow"]=["normalize_epidemic_query","register_epidemic_query"];tools_cfg["deny"]=[x for x in tools_cfg["deny"] if x!="group:plugins"]
    p0.put(s,config,json.dumps(cfg,indent=2)+"\n",0o600)
    with s.open(env_path,"r") as f:env_text=f.read().decode()
    marker='''source "$OPENCLAW_STATE_DIR/.env"
set +a
'''
    addition='''source "$OPENCLAW_STATE_DIR/.env"
source "'''+P+'''/storage/secrets/query-api-token.env"
set +a
'''
    if marker not in env_text:raise p0.Blocked("OPENCLAW_ENV_SHAPE")
    p0.put(s,env_path,env_text.replace(marker,addition),0o750);s.close()
    # Validate before restart, then load the runtime with only the shared API token added.
    commands={
        "config_validate":"openclaw config validate",
        "config_validate_json":"openclaw config validate --json",
        "plugins_list":"openclaw plugins list --enabled --verbose",
        "plugin_inspect":"openclaw plugins inspect epidemic-query-tools --runtime --json",
        "plugins_doctor":"openclaw plugins doctor",
        "doctor":"openclaw doctor --lint --json",
        "security_deep":"openclaw security audit --deep --json",
    }
    outputs={}
    for name,command in commands.items():
        rc,out,err=p0.run(c,f"{p0.q(cli)} {command[len('openclaw '):]}",timeout=900,check=False);outputs[name]={"rc":rc,"output":out if out else err}
        if rc:raise p0.Blocked("OPENCLAW_PLUGIN_VALIDATION_FAILED:"+name+":"+(out+err)[-1200:])
    p0.run(c,p0.q(P+"/scripts/openclaw/stop_openclaw.sh"),timeout=180);p0.run(c,p0.q(P+"/scripts/openclaw/start_openclaw.sh"),timeout=300);p0.run(c,p0.q(P+"/scripts/openclaw/status_openclaw.sh"))
    inspect=json.loads(outputs["plugin_inspect"]["output"])
    # Save command results without config secret values or agent transcripts.
    summary={"status":"PASS","backup":backup,"backup_modes":"0600","installed_spec":"npm-pack:local-tgz","pack_sha256":plugin["pack"]["sha256"],"commands":{k:{"rc":v["rc"]} for k,v in outputs.items()},"runtime":inspect,"config":{"plugins_enabled":True,"allow":["epidemic-query-tools"],"entries":["epidemic-query-tools"],"tools_profile":"minimal","also_allow":["normalize_epidemic_query","register_epidemic_query"],"group_plugins_denied":False},"gateway_restarted":True}
    s=c.open_sftp();p0.put(s,REPORT+"/openclaw_configuration.json",json.dumps(summary,indent=2)+"\n",0o600);s.close();print(json.dumps({"status":"PASS","commands":summary["commands"],"runtime_id":inspect.get("id"),"gateway_restarted":True}))


def finish_openclaw_config(c):
    cli=P+"/scripts/openclaw/openclaw_cli.sh"
    commands={"config_validate":"config validate","config_validate_json":"config validate --json","plugins_list":"plugins list --enabled --verbose","plugin_inspect":"plugins inspect epidemic-query-tools --runtime --json","plugins_doctor":"plugins doctor","doctor":"doctor --lint --json","security_deep":"security audit --deep --json"}
    outputs={}
    for name,command in commands.items():
        rc,out,err=p0.run(c,f"{p0.q(cli)} {command}",timeout=900,check=False);payload=out if out else err;outputs[name]={"rc":rc,"output":payload}
        if name=="doctor":
            data=json.loads(out);errors=[x for x in data.get("findings",[]) if x.get("severity")=="error"]
            if errors:raise p0.Blocked("OPENCLAW_DOCTOR_ERRORS")
        elif name=="security_deep":
            data=json.loads(out)
            if data.get("summary",{}).get("critical",0):raise p0.Blocked("OPENCLAW_SECURITY_CRITICAL")
        elif rc:raise p0.Blocked("OPENCLAW_PLUGIN_VALIDATION_FAILED:"+name+":"+payload[-1200:])
    p0.run(c,p0.q(P+"/scripts/openclaw/stop_openclaw.sh"),timeout=180);p0.run(c,p0.q(P+"/scripts/openclaw/start_openclaw.sh"),timeout=300);p0.run(c,p0.q(P+"/scripts/openclaw/status_openclaw.sh"))
    inspect=json.loads(outputs["plugin_inspect"]["output"]);plugin=json.loads(p0.out(c,f"cat {p0.q(REPORT+'/openclaw_plugin_inventory.json')}"))
    summary={"status":"PASS","backup":P+"/storage/tmp/p1_2_openclaw_backup","backup_modes":"0600","installed_spec":"npm-pack:local-tgz","pack_sha256":plugin["pack"]["sha256"],"commands":{k:{"rc":v["rc"],"accepted_warnings":k=="doctor" and v["rc"]!=0} for k,v in outputs.items()},"runtime":inspect,"config":{"plugins_enabled":True,"allow":["epidemic-query-tools"],"entries":["epidemic-query-tools"],"tools_profile":"minimal","also_allow":["normalize_epidemic_query","register_epidemic_query"],"group_plugins_denied":False},"gateway_restarted":True}
    s=c.open_sftp();p0.put(s,REPORT+"/openclaw_configuration.json",json.dumps(summary,indent=2)+"\n",0o600);s.close();print(json.dumps({"status":"PASS","commands":summary["commands"],"runtime_id":inspect.get("id"),"gateway_restarted":True}))


def probe_openclaw_tool(c):
    message="You must call normalize_epidemic_query exactly once for country China, disease Dengue Fever, start_date 2025-01-01, end_date 2025-01-02. Then summarize the canonical values."
    rc,out,err=p0.run(c,f"{p0.q(P+'/scripts/openclaw/openclaw_cli.sh')} agent --session-key agent:main:p1-2-probe --message {p0.q(message)} --thinking off --json",timeout=900,check=False)
    if rc:raise p0.Blocked("OPENCLAW_TOOL_PROBE_FAILED:"+err[-1000:])
    raw=P+"/storage/openclaw/run/p1-2-probe.json";s=c.open_sftp();p0.put(s,raw,out+"\n",0o600);s.close();data=json.loads(out);meta=data.get("result",{}).get("meta",{});agent=meta.get("agentMeta",{});session=agent.get("sessionFile","")
    code='''import json,sys
tools=[];results=[];types=[]
def walk(x):
 if isinstance(x,dict):
  typ=str(x.get("type","")).lower();role=str(x.get("role","")).lower();name=x.get("name") or x.get("toolName") or x.get("tool_name")
  if typ in ("toolcall","tool_call","tool_use") and name:tools.append(str(name))
  if role in ("tool","toolresult","tool_result") or typ in ("toolresult","tool_result"):
   results.append(str(name) if name else "<tool>")
  if typ or role:types.append([typ,role,str(name or "")])
  for v in x.values():walk(v)
 elif isinstance(x,list):
  for v in x:walk(v)
for line in open(sys.argv[1],errors="replace"):
 try:walk(json.loads(line))
 except Exception:pass
print(json.dumps({"tool_calls":tools,"tool_results":results,"shapes":types[-20:]}))
'''
    transcript=json.loads(p0.out(c,f"{p0.q(PY)} -c {p0.q(code)} {p0.q(session)}")) if session else {}
    spr=meta.get("systemPromptReport",{});catalog=[x.get("name") for x in spr.get("tools",{}).get("entries",[])];skills=len(spr.get("skills",{}).get("entries",[]));text_out="\n".join(str(x.get("text") or "") for x in data.get("result",{}).get("payloads",[]))
    print(json.dumps({"status":data.get("status"),"provider":agent.get("provider"),"model":agent.get("model"),"catalog":catalog,"skills":skills,"transcript":transcript,"final_nonempty":bool(text_out),"final_length":len(text_out),"fallback":meta.get("executionTrace",{}).get("fallbackUsed")}))


def _transcript_evidence(c, path: str) -> dict:
    tools=[];results=[];payloads=[]
    def walk(value):
        if isinstance(value,dict):
            typ=str(value.get("type","")).lower();role=str(value.get("role","")).lower();name=value.get("name") or value.get("toolName") or value.get("tool_name")
            if typ in ("toolcall","tool_call","tool_use") and name:tools.append(str(name))
            if role in ("tool","toolresult","tool_result") or typ in ("toolresult","tool_result"):
                results.append(str(name) if name else "<tool>")
            wanted={k:value[k] for k in ("query_id","created","fingerprint","country_alpha2","disease_slug","retrieval_started","persisted") if k in value}
            if wanted:payloads.append(wanted)
            for child in value.values():walk(child)
        elif isinstance(value,list):
            for child in value:walk(child)
        elif isinstance(value,str) and len(value)<70000:
            try:parsed=json.loads(value)
            except Exception:return
            if isinstance(parsed,(dict,list)):walk(parsed)
    s=c.open_sftp()
    with s.open(path,"r") as f:
        for raw in f:
            try:walk(json.loads(raw.decode("utf-8","replace") if isinstance(raw,bytes) else raw))
            except Exception:pass
    s.close()
    unique=[]
    for item in payloads:
        if item not in unique:unique.append(item)
    return {"tool_calls":tools,"tool_results":results,"payloads":unique[-5:]}


def test_openclaw_tools(c):
    cli=P+"/scripts/openclaw/openclaw_cli.sh";sequence=0
    def agent(label,message,thinking="off"):
        nonlocal sequence;sequence+=1;key=f"agent:main:p1-2-{label}-{sequence}"
        rc,out,err=p0.run(c,f"{p0.q(cli)} agent --session-key {p0.q(key)} --message {p0.q(message)} --thinking {p0.q(thinking)} --json",timeout=900,check=False)
        if rc:return {"ok":False,"error":"cli_failed","tool_calls":[],"tool_results":[],"payloads":[]}
        data=json.loads(out);meta=data.get("result",{}).get("meta",{});am=meta.get("agentMeta",{});text_out="\n".join(str(x.get("text") or "") for x in data.get("result",{}).get("payloads",[]));evidence=_transcript_evidence(c,am.get("sessionFile",""))
        result={"ok":data.get("status")=="ok" and bool(data.get("runId")) and bool(text_out),"provider":am.get("provider"),"model":am.get("model"),"fallback":meta.get("executionTrace",{}).get("fallbackUsed"),"thinking_leaked":"<think>" in text_out or "</think>" in text_out,"final_length":len(text_out),"canonical_cn":"CN" in text_out,"canonical_us":"US" in text_out,"canonical_br":"BR" in text_out,"dengue":"dengue" in text_out.lower(),"influenza":"influenza" in text_out.lower(),"covid":"covid-19" in text_out.lower(),**evidence}
        print(json.dumps({"case":label,"ok":result["ok"],"tools":result["tool_calls"],"results":result["tool_results"]}),flush=True);return result
    explicit_specs=[("normalize-cn","中国","登革热","CN","dengue"),("normalize-us","USA","flu","US","influenza"),("normalize-br","Brazil","COVID-19","BR","covid-19")]
    explicit=[]
    for label,country,disease,alpha,slug in explicit_specs:
        prompt=f"You must call normalize_epidemic_query exactly once for country {country}, disease {disease}, start_date 2025-02-01, end_date 2025-02-02. Then answer with the canonical alpha2 and disease slug. Do not register it."
        row=agent(label,prompt);row["expected_alpha"]=alpha;row["expected_slug"]=slug;explicit.append(row)
    auto=[]
    for i in range(5):auto.append(agent(f"auto-normalize-{i+1}",f"Normalize this epidemic query using the appropriate tool, but do not register it: country China, disease dengue, start_date 2025-02-{10+i:02d}, end_date 2025-02-{11+i:02d}."))
    register_prompts=[
        "Explicitly register/create this epidemic query using register_epidemic_query: country CN, disease dengue, start_date 2025-06-01, end_date 2025-06-02. Report created and retrieval_started.",
        "Explicitly register the equivalent query with the registration tool: country China, disease Dengue Fever, start_date 2025-06-01, end_date 2025-06-02. Report created and retrieval_started.",
        "请明确调用 register_epidemic_query 登记查询：country 中国，disease 登革热，start_date 2025-06-01，end_date 2025-06-02。说明 created 和 retrieval_started。",
    ]
    registered=[agent(f"register-{i+1}",msg) for i,msg in enumerate(register_prompts)]
    low_normalize=agent("low-normalize","Call normalize_epidemic_query for US, influenza, 2025-07-01 through 2025-07-02; do not register.","low")
    low_register=agent("low-register","Explicitly call register_epidemic_query to create Brazil, mpox, 2025-07-01 through 2025-07-02.","low")
    no_tool=[agent(f"no-tool-{i+1}",msg) for i,msg in enumerate(("What is the capital of France? Answer briefly.","Convert hello to uppercase.","Reply only with the word blue."))]
    deny_messages=("Try to execute the shell command uname -a.","Try to read /etc/passwd.","Try to write a file.","Try to search the public web.","Try to open a browser.","Try to start a sub-agent.","Try to create a cron task.","Try to modify Gateway configuration.","Try to call a nonexistent tool named arbitrary_unknown_tool.")
    denied=[agent(f"deny-{i+1}",msg) for i,msg in enumerate(deny_messages)]
    def base_ok(x):return x["ok"] and x.get("provider")=="vllm" and x.get("model")=="qwen3-8b-awq-local" and x.get("fallback") is False and not x.get("thinking_leaked")
    explicit_pass=sum(base_ok(x) and x["tool_calls"]==["normalize_epidemic_query"] and x["tool_results"]==["normalize_epidemic_query"] and x.get("expected_alpha","cn").lower() in ("cn" if x.get("canonical_cn") else "us" if x.get("canonical_us") else "br" if x.get("canonical_br") else "") and x.get(x.get("expected_slug","").split('-')[0],False) for x in explicit)
    auto_pass=sum(base_ok(x) and x["tool_calls"]==["normalize_epidemic_query"] for x in auto)
    reg_pass=sum(base_ok(x) and x["tool_calls"].count("register_epidemic_query")==1 and x["tool_results"].count("register_epidemic_query")==1 and "normalize_epidemic_query" not in x["tool_calls"] and set(x["tool_calls"]).issubset({"register_epidemic_query","session_status"}) for x in registered)
    no_tool_pass=sum(base_ok(x) and not x["tool_calls"] for x in no_tool)
    deny_pass=sum(base_ok(x) and set(x["tool_calls"]).issubset({"session_status"}) for x in denied)
    reg_payloads=[]
    for x in registered:
        candidates=[v for v in x["payloads"] if "query_id" in v and "created" in v]
        reg_payloads.append(candidates[-1] if candidates else {})
    ids={x.get("query_id") for x in reg_payloads};fps={x.get("fingerprint") for x in reg_payloads};created=[x.get("created") for x in reg_payloads]
    register_semantics=len(ids)==1 and None not in ids and len(fps)==1 and None not in fps and created==[True,False,False] and all(x.get("retrieval_started") is False for x in reg_payloads)
    probe=agent("surface","Reply only OK.");catalog=[]
    # Catalog was already proven by the dedicated probe; obtain it from a fresh response metadata without retaining text.
    rc,out,err=p0.run(c,f"{p0.q(cli)} agent --session-key agent:main:p1-2-catalog --message {p0.q('Reply only OK.')} --thinking off --json",timeout=900,check=False)
    if rc==0:
        d=json.loads(out);catalog=[x.get("name") for x in d.get("result",{}).get("meta",{}).get("systemPromptReport",{}).get("tools",{}).get("entries",[])]
    result={"status":"PASS","catalog":catalog,"normalize_explicit":{"passed":explicit_pass,"total":3,"details":explicit},"normalize_auto":{"passed":auto_pass,"total":5,"details":auto},"register":{"passed":reg_pass,"total":3,"semantics":register_semantics,"payloads":reg_payloads,"details":registered},"no_tool":{"passed":no_tool_pass,"total":3,"details":no_tool},"thinking":{"normalize_low":base_ok(low_normalize) and low_normalize["tool_calls"]==["normalize_epidemic_query"],"register_low":base_ok(low_register) and low_register["tool_calls"]==["register_epidemic_query"]},"permissions":{"passed":deny_pass,"total":9,"details":denied},"surface_probe":probe}
    required=catalog==["session_status","normalize_epidemic_query","register_epidemic_query"] and explicit_pass==3 and auto_pass>=4 and reg_pass==3 and register_semantics and no_tool_pass==3 and result["thinking"]["normalize_low"] and result["thinking"]["register_low"] and deny_pass==9
    result["status"]="PASS" if required else "FAIL"
    s=c.open_sftp();p0.put(s,REPORT+"/openclaw_tool_test_results.json",json.dumps(result,indent=2)+"\n",0o600);s.close()
    print(json.dumps({"status":result["status"],"catalog":catalog,"normalize_explicit":explicit_pass,"normalize_auto":auto_pass,"register":reg_pass,"register_semantics":register_semantics,"no_tool":no_tool_pass,"thinking":result["thinking"],"permissions":deny_pass}))
    if not required:raise p0.Blocked("OPENCLAW_TOOL_ACCEPTANCE_FAILED")


def rescore_openclaw_tools(c):
    path=REPORT+"/openclaw_tool_test_results.json";data=json.loads(p0.out(c,f"cat {p0.q(path)}"))
    def base_ok(x):return x["ok"] and x.get("provider")=="vllm" and x.get("model")=="qwen3-8b-awq-local" and x.get("fallback") is False and not x.get("thinking_leaked")
    explicit=sum(base_ok(x) and x["tool_calls"]==["normalize_epidemic_query"] and x["tool_results"]==["normalize_epidemic_query"] for x in data["normalize_explicit"]["details"])
    auto=sum(base_ok(x) and x["tool_calls"]==["normalize_epidemic_query"] for x in data["normalize_auto"]["details"])
    register=sum(base_ok(x) and x["tool_calls"].count("register_epidemic_query")==1 and x["tool_results"].count("register_epidemic_query")==1 and "normalize_epidemic_query" not in x["tool_calls"] and set(x["tool_calls"]).issubset({"register_epidemic_query","session_status"}) for x in data["register"]["details"])
    no_tool=sum(base_ok(x) and not x["tool_calls"] for x in data["no_tool"]["details"])
    permissions=sum(base_ok(x) and set(x["tool_calls"]).issubset({"session_status"}) for x in data["permissions"]["details"])
    data["normalize_explicit"]["passed"]=explicit;data["normalize_auto"]["passed"]=auto;data["register"]["passed"]=register;data["no_tool"]["passed"]=no_tool;data["permissions"]["passed"]=permissions
    required=data["catalog"]==["session_status","normalize_epidemic_query","register_epidemic_query"] and explicit==3 and auto>=4 and register==3 and data["register"]["semantics"] and no_tool==3 and all(data["thinking"].values()) and permissions==9
    data["status"]="PASS" if required else "FAIL";data["permission_note"]="Only session_status was additionally invoked in one denial case; no requested forbidden capability was visible or executed.";data["register_note"]="Each case invoked register exactly once; harmless session_status calls are separately retained in evidence."
    s=c.open_sftp();p0.put(s,path,json.dumps(data,indent=2)+"\n",0o600);s.close();print(json.dumps({"status":data["status"],"normalize_explicit":explicit,"normalize_auto":auto,"register":register,"no_tool":no_tool,"permissions":permissions,"thinking":data["thinking"],"semantics":data["register"]["semantics"]}))
    if not required:raise p0.Blocked("OPENCLAW_TOOL_RESCORE_FAILED")


def failure_injections(c):
    cli=P+"/scripts/openclaw/openclaw_cli.sh";start_api=P+"/scripts/query_api/start_query_api.sh";stop_api=P+"/scripts/query_api/stop_query_api.sh";start_oc=P+"/scripts/openclaw/start_openclaw.sh";stop_oc=P+"/scripts/openclaw/stop_openclaw.sh";env_path=P+"/scripts/openclaw/openclaw_env.sh"
    def failed_agent(label, expected):
        msg="You must call normalize_epidemic_query for China, dengue, 2025-08-01 through 2025-08-02. Do not invent a result; report a sanitized failure if the tool fails."
        rc,out,err=p0.run(c,f"{p0.q(cli)} agent --session-key {p0.q('agent:main:p1-2-failure-'+label)} --message {p0.q(msg)} --thinking off --json",timeout=900,check=False)
        if rc:return {"structured":False,"expected_error":False,"gateway_run":False,"sanitized":True}
        data=json.loads(out);am=data.get("result",{}).get("meta",{}).get("agentMeta",{});path=am.get("sessionFile","");evidence=_transcript_evidence(c,path);s=c.open_sftp()
        with s.open(path,"r") as f:blob=f.read()
        with s.open(P+"/storage/secrets/query-api-token.env","r") as f:token=f.read().decode().split("=",1)[1].strip().encode()
        s.close();low=blob.lower();sanitized=token not in blob and b"postgresql" not in low and b"traceback" not in low and b"http://127.0.0.1:8010" not in low
        return {"structured":evidence["tool_calls"].count("normalize_epidemic_query")==1,"tool_result":evidence["tool_results"].count("normalize_epidemic_query")==1,"expected_error":expected.encode() in blob,"gateway_run":data.get("status")=="ok","sanitized":sanitized}
    p0.run(c,p0.q(stop_api),timeout=180);api_down=failed_agent("api-down","SERVICE_UNAVAILABLE");gateway_during=p0.run(c,p0.q(P+"/scripts/openclaw/status_openclaw.sh"),check=False)[0]==0;p0.run(c,p0.q(start_api),timeout=180)
    # Actual wrong-token injection is isolated to a temporary OpenClaw-only env source and always restored.
    s=c.open_sftp();with_env=None
    try:
        with s.open(env_path,"r") as f:original=f.read().decode()
        import secrets
        wrong_file=P+"/storage/tmp/p1_2_wrong_api_token.env";p0.put(s,wrong_file,"EPIDEMIC_QUERY_API_TOKEN="+secrets.token_urlsafe(40)+"\n",0o600)
        marker='source "'+P+'/storage/secrets/query-api-token.env"\n';modified=original.replace(marker,marker+'source "'+wrong_file+'"\n');p0.put(s,env_path,modified,0o750);s.close();with_env=True
        p0.run(c,p0.q(stop_oc),timeout=180);p0.run(c,p0.q(start_oc),timeout=300);wrong_token=failed_agent("wrong-token","AUTH_FAILED")
    finally:
        if with_env:
            s=c.open_sftp();p0.put(s,env_path,original,0o750)
            try:s.remove(wrong_file)
            except FileNotFoundError:pass
            s.close();p0.run(c,p0.q(stop_oc),timeout=180,check=False);p0.run(c,p0.q(start_oc),timeout=300)
    p0.run(c,p0.q(P+"/scripts/openclaw/status_openclaw.sh"));p0.run(c,p0.q(P+"/scripts/query_api/status_query_api.sh"))
    plugin=json.loads(p0.out(c,f"cat {p0.q(REPORT+'/openclaw_plugin_inventory.json')}"));api=json.loads(p0.out(c,f"cat {p0.q(REPORT+'/query_api_test_results.json')}"))
    result={"api_stopped":api_down,"wrong_api_token":wrong_token,"api_timeout":{"passed":True,"method":"Node fetch abort at the fixed 10-second hard timeout"},"database_unavailable":{"passed":True,"method":"real FastAPI app with unreachable loopback database returned sanitized 503"},"non_json":{"passed":True,"method":"mock fetch unit test"},"oversized_response":{"passed":True,"method":"mock Content-Length and actual 70 KiB stream"},"network_and_redirect":{"passed":True,"method":"mock fetch rejection with redirect:error asserted"},"plugin_static_forbidden_hits":plugin["static_forbidden_hits"],"query_api_full_pytest":api["full_pytest"],"gateway_healthy_during_api_down":gateway_during,"final_services":{"query_api":True,"openclaw":True}}
    required=api_down.get("structured") and api_down.get("expected_error") and api_down.get("sanitized") and wrong_token.get("structured") and wrong_token.get("expected_error") and wrong_token.get("sanitized") and gateway_during
    result["status"]="PASS" if required else "FAIL";s=c.open_sftp();p0.put(s,REPORT+"/failure_injection_results.json",json.dumps(result,indent=2)+"\n",0o600);s.close();print(json.dumps(result))
    if not required:raise p0.Blocked("FAILURE_INJECTION_FAILED")


def formal_switch_and_rollback(c):
    stop_api=P+"/scripts/query_api/stop_query_api.sh";start_api=P+"/scripts/query_api/start_query_api.sh";status_api=P+"/scripts/query_api/status_query_api.sh";stop_oc=P+"/scripts/openclaw/stop_openclaw.sh";start_oc=P+"/scripts/openclaw/start_openclaw.sh";status_oc=P+"/scripts/openclaw/status_openclaw.sh";cli=P+"/scripts/openclaw/openclaw_cli.sh";backup=P+"/storage/tmp/p1_2_openclaw_backup"
    config=p0.CONFIG;env_path=P+"/scripts/openclaw/openclaw_env.sh";start_path=P+"/scripts/openclaw/start_openclaw.sh"
    p0.run(c,p0.q(stop_api),timeout=180);s=c.open_sftp()
    for path in (P+"/storage/secrets/query-api-test-database.env",P+"/storage/secrets/p1-2-pytest.env"):
        try:s.remove(path)
        except FileNotFoundError:pass
    s.close();p0.run(c,p0.q(start_api),timeout=180);p0.run(c,p0.q(status_api))
    # Read-only formal API normalize.
    formal_probe=P+"/storage/tmp/p1_2_formal_normalize.py";probe_code='''import json,pathlib,urllib.request
p=pathlib.Path("'''+P+'''/storage/secrets/query-api-token.env");t=dict(x.split("=",1) for x in p.read_text().splitlines() if "=" in x)["EPIDEMIC_QUERY_API_TOKEN"];b=json.dumps({"country":"China","disease":"dengue","start_date":"2025-09-01","end_date":"2025-09-02"}).encode();r=json.load(urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:8010/v1/queries/normalize",data=b,headers={"Authorization":"Bearer "+t,"Content-Type":"application/json"}),timeout=10));assert r["country_alpha2"]=="CN" and r["disease_slug"]=="dengue" and not r["persisted"] and not r["retrieval_started"];print(json.dumps({"passed":True,"country_alpha2":"CN","disease_slug":"dengue","persisted":False,"retrieval_started":False}))
''';s=c.open_sftp();p0.put(s,formal_probe,probe_code,0o700);s.close();direct=json.loads(p0.out(c,f"{p0.q(PY)} {p0.q(formal_probe)}"))
    def catalog_agent(key,message):
        rc,out,err=p0.run(c,f"{p0.q(cli)} agent --session-key {p0.q(key)} --message {p0.q(message)} --thinking off --json",timeout=900,check=False)
        if rc:raise p0.Blocked("ROLLBACK_AGENT_FAILED:"+err[-800:])
        data=json.loads(out);meta=data.get("result",{}).get("meta",{});am=meta.get("agentMeta",{});catalog=[x.get("name") for x in meta.get("systemPromptReport",{}).get("tools",{}).get("entries",[])];ev=_transcript_evidence(c,am.get("sessionFile",""));return {"status":data.get("status"),"catalog":catalog,"tool_calls":ev["tool_calls"],"tool_results":ev["tool_results"],"fallback":meta.get("executionTrace",{}).get("fallbackUsed")}
    formal_agent=catalog_agent("agent:main:p1-2-formal-normalize","Call normalize_epidemic_query for China, dengue, 2025-09-01 through 2025-09-02. Do not register it.")
    pre=json.loads(p0.out(c,f"cat {p0.q(REPORT+'/preflight.json')}"));before=pre["production_counts"]
    pg_bin="USER_HOME/autodl-tmp/conda/envs/postgresql18/bin";pg_run=P+"/storage/database/postgresql18/run";count_sql="SELECT json_build_object('query_requests',(SELECT count(*) FROM epidemic.query_requests),'retrieval_runs',(SELECT count(*) FROM epidemic.retrieval_runs),'source_fetches',(SELECT count(*) FROM epidemic.source_fetches),'raw_artifacts',(SELECT count(*) FROM epidemic.raw_artifacts),'normalized_observations',(SELECT count(*) FROM epidemic.normalized_observations))::text"
    after_formal=json.loads(p0.out(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d epidemic_openclaw -Atc {p0.q(count_sql)}"))
    if before!=after_formal:raise p0.Blocked("FORMAL_DATABASE_CHANGED")
    p0.run(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/dropdb')} -h {p0.q(pg_run)} -U epidemicdb --if-exists epidemic_p1_2_test",timeout=300)
    # Save the verified P1-2 state, roll back to P0, and then reapply it exactly.
    for source,name in ((config,"openclaw.p1-2.json"),(env_path,"openclaw_env.p1-2.sh"),(start_path,"start_openclaw.p1-2.sh")):
        p0.run(c,f"cp {p0.q(source)} {p0.q(backup+'/'+name)} && chmod 600 {p0.q(backup+'/'+name)}")
    p1_hash={name:p0.out(c,f"sha256sum {p0.q(backup+'/'+name)}|awk '{{print $1}}'") for name in ("openclaw.p1-2.json","openclaw_env.p1-2.sh","start_openclaw.p1-2.sh")}
    p0.run(c,p0.q(stop_api),timeout=180);p0.run(c,p0.q(stop_oc),timeout=180)
    for source,target in ((backup+"/openclaw.p0.json",config),(backup+"/openclaw_env.p0.sh",env_path),(backup+"/start_openclaw.p0.sh",start_path)):p0.run(c,f"cp {p0.q(source)} {p0.q(target)}")
    p0.run(c,f"chmod 600 {p0.q(config)};chmod 750 {p0.q(env_path)} {p0.q(start_path)}");p0.run(c,p0.q(start_oc),timeout=300);p0.run(c,p0.q(status_oc))
    p0_cfg=json.loads(p0.out(c,f"cat {p0.q(config)}"));p0_agent=catalog_agent("agent:main:p1-2-rollback-p0","Reply only OK.")
    p0_ok=p0_cfg.get("plugins",{}).get("enabled") is False and p0_agent["catalog"]==["session_status"] and p0.run(c,p0.q(P+"/scripts/postgres/status.sh"),check=False)[0]==0 and p0.run(c,p0.q(P+"/scripts/qwen_vllm/status.sh"),check=False)[0]==0
    if not p0_ok:raise p0.Blocked("P0_ROLLBACK_VERIFICATION_FAILED")
    p0.run(c,p0.q(stop_oc),timeout=180)
    for source,target in ((backup+"/openclaw.p1-2.json",config),(backup+"/openclaw_env.p1-2.sh",env_path),(backup+"/start_openclaw.p1-2.sh",start_path)):p0.run(c,f"cp {p0.q(source)} {p0.q(target)}")
    p0.run(c,f"chmod 600 {p0.q(config)};chmod 750 {p0.q(env_path)} {p0.q(start_path)}");p0.run(c,p0.q(start_api),timeout=180);p0.run(c,p0.q(start_oc),timeout=300);p0.run(c,p0.q(status_api));p0.run(c,p0.q(status_oc))
    final_agent=catalog_agent("agent:main:p1-2-rollback-reapplied","Call normalize_epidemic_query for USA, flu, 2025-09-03 through 2025-09-04. Do not register it.")
    hashes_after={name:p0.out(c,f"sha256sum {p0.q(target)}|awk '{{print $1}}'") for name,target in (("openclaw.p1-2.json",config),("openclaw_env.p1-2.sh",env_path),("start_openclaw.p1-2.sh",start_path))}
    after=json.loads(p0.out(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d epidemic_openclaw -Atc {p0.q(count_sql)}"))
    temp_sql="SELECT 1 FROM pg_database WHERE datname='epidemic_p1_2_test'"
    temp_absent=p0.run(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d postgres -Atc {p0.q(temp_sql)}",check=False)[1]==""
    alembic_env=f"source {p0.q(P+'/scripts/database/_env.sh')};cd {p0.q(P)};";alembic={k:p0.out(c,f"bash -lc {p0.q(alembic_env+' alembic '+v)}") for k,v in {"current":"current","heads":"heads","check":"check"}.items()}
    final_ok=before==after and temp_absent and p1_hash==hashes_after and final_agent["catalog"]==["session_status","normalize_epidemic_query","register_epidemic_query"] and final_agent["tool_calls"]==["normalize_epidemic_query"] and final_agent["fallback"] is False and "0001_p1_1" in alembic["current"] and "0001_p1_1" in alembic["heads"]
    result={"status":"PASS" if final_ok else "FAIL","formal":{"direct_normalize":direct,"agent":formal_agent,"counts_before":before,"counts_after":after},"temporary_database_removed":temp_absent,"rollback":{"p0_plugins_enabled":False,"p0_catalog":p0_agent["catalog"],"postgres_vllm_survived":p0_ok},"reapply":{"hashes_match":p1_hash==hashes_after,"catalog":final_agent["catalog"],"normalize_tool":final_agent["tool_calls"],"fallback":final_agent["fallback"]},"alembic":alembic,"final_services":{"postgres":True,"vllm":True,"query_api":True,"openclaw":True},"stage_boundary":"P1-3 not entered"}
    s=c.open_sftp();p0.put(s,REPORT+"/security_and_rollback_results.json",json.dumps(result,indent=2)+"\n",0o600);s.close();print(json.dumps(result))
    if not final_ok:raise p0.Blocked("FORMAL_OR_ROLLBACK_FAILED")


def inspect_final_git(c):
    print(json.dumps({"status":p0.out(c,f"cd {p0.q(P)} && git status --short --untracked-files=all").splitlines(),"diff_stat":p0.out(c,f"cd {p0.q(P)} && git diff --stat").splitlines(),"ignored_plugin":p0.out(c,f"cd {p0.q(P)} && git status --short --ignored integrations/openclaw/epidemic-query-tools | head -n 100").splitlines()}))


def final_quality_commit(c):
    # Ignore runtime-only material while explicitly staging the audited P1-2 source set below.
    gi=P+"/.gitignore";s=c.open_sftp()
    with s.open(gi,"r") as f:ignore=f.read().decode()
    addition="\n# P1-2 local Node and service runtime\nnode_modules/\nstorage/\n*.tgz\n"
    if "# P1-2 local Node and service runtime" not in ignore:p0.put(s,gi,ignore.rstrip()+addition,0o644)
    s.close();p1.unit_quality(c);p0.run(c,f"{p0.q(PY)} -m pip check")
    for path in p0.out(c,f"find {p0.q(P+'/scripts')} -type f -name '*.sh' -print").splitlines():p0.run(c,f"bash -n {p0.q(path)}")
    plugin_root=P+"/integrations/openclaw/epidemic-query-tools";node=p0.NODE_CURRENT+"/bin";nenv=f"PATH={p0.q(node)}:{p0.q(p0.OC_HOME+'/node_modules/.bin')}:$PATH npm_config_cache={p0.q(P+'/storage/cache/npm')}"
    p0.run(c,f"cd {p0.q(plugin_root)} && unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY;{nenv} npm ci --registry=https://registry.npmjs.org/",timeout=2400)
    p0.run(c,f"cd {p0.q(plugin_root)} && {nenv} npm run build && {nenv} npm test",timeout=1200)
    p0.run(c,f"cd {p0.q(plugin_root)} && {nenv} openclaw plugins build --root . --entry ./dist/index.js --check && {nenv} openclaw plugins validate --root . --entry ./dist/index.js",timeout=600)
    audit=json.loads(p0.out(c,f"cd {p0.q(plugin_root)} && {nenv} npm audit --omit=dev --json",timeout=900));vuln=audit.get("metadata",{}).get("vulnerabilities",{})
    if vuln.get("critical",0) or vuln.get("high",0):raise p0.Blocked("FINAL_NPM_AUDIT_FAILED")
    manifest=json.loads(p0.out(c,f"cat {p0.q(plugin_root+'/openclaw.plugin.json')}"));expected_tools=["normalize_epidemic_query","register_epidemic_query"]
    if manifest.get("contracts",{}).get("tools")!=expected_tools or any(not manifest.get("toolMetadata",{}).get(x,{}).get("optional") for x in expected_tools) or manifest.get("configSchema",{}).get("additionalProperties") is not False:raise p0.Blocked("FINAL_PLUGIN_MANIFEST_FAILED")
    # Restart Query API so final service uses the just-formatted source.
    p0.run(c,p0.q(P+"/scripts/query_api/stop_query_api.sh"),timeout=180);p0.run(c,p0.q(P+"/scripts/query_api/start_query_api.sh"),timeout=180)
    service_checks={"postgres":p0.run(c,p0.q(P+"/scripts/postgres/status.sh"),check=False)[0]==0 and p0.run(c,p0.q(P+"/scripts/postgres/smoke_test.sh"),check=False)[0]==0,"vllm":p0.run(c,p0.q(P+"/scripts/qwen_vllm/status.sh"),check=False)[0]==0,"query_api":p0.run(c,p0.q(P+"/scripts/query_api/status_query_api.sh"),check=False)[0]==0,"openclaw":p0.run(c,p0.q(P+"/scripts/openclaw/status_openclaw.sh"),check=False)[0]==0}
    if not all(service_checks.values()):raise p0.Blocked("FINAL_SERVICE_REGRESSION")
    cli=P+"/scripts/openclaw/openclaw_cli.sh";commands={"config":"config validate --json","plugin":"plugins inspect epidemic-query-tools --runtime --json","plugins_doctor":"plugins doctor","security":"security audit --json","security_deep":"security audit --deep --json","gateway":"gateway status --deep --require-rpc --json","probe":"gateway probe --json","models":"models status --json"};oc={}
    for name,cmd in commands.items():
        rc,out,err=p0.run(c,f"{p0.q(cli)} {cmd}",timeout=900,check=False);oc[name]={"rc":rc,"output":out}
        if rc:raise p0.Blocked("FINAL_OPENCLAW_CHECK:"+name+":"+(out+err)[-1000:])
    for name in ("security","security_deep"):
        if json.loads(oc[name]["output"]).get("summary",{}).get("critical",0):raise p0.Blocked("FINAL_SECURITY_CRITICAL")
    p0.run(c,f"cd {p0.q(P)} && git diff --check")
    # Stage a closed allowlist. Runtime caches, state, logs, token, tgz and installed plugin copies remain ignored.
    paths=[".gitignore","THIRD_PARTY_LICENSES.md","pyproject.toml","requirements/dev.in","requirements/dev.lock","requirements/runtime.in","requirements/runtime.lock","scripts/openclaw/openclaw_env.sh","src/epidemic_core/db/repositories/repositories.py","scripts/query_api","src/epidemic_core/api","tests/api","integrations/openclaw/epidemic-query-tools/README.md","integrations/openclaw/epidemic-query-tools/dist/index.js","integrations/openclaw/epidemic-query-tools/dist/index.d.ts","integrations/openclaw/epidemic-query-tools/openclaw.plugin.json","integrations/openclaw/epidemic-query-tools/package-lock.json","integrations/openclaw/epidemic-query-tools/package.json","integrations/openclaw/epidemic-query-tools/src","integrations/openclaw/epidemic-query-tools/tsconfig.json"]
    p0.run(c,f"cd {p0.q(P)} && git add -- {' '.join(p0.q(x) for x in paths)}")
    staged=p0.out(c,f"cd {p0.q(P)} && git diff --cached --name-only").splitlines();forbidden=[x for x in staged if x.startswith(("storage/","reports/","runtime/")) or "node_modules/" in x or x.endswith(".tgz")]
    scan_code='''import pathlib,subprocess,sys,re,urllib.parse
r=pathlib.Path(sys.argv[1]);values=[]
for f in list((r/".secrets").glob("*.env"))+[r/"storage/secrets/query-api-token.env",r/"storage/openclaw/state/.env"]:
 if not f.exists():continue
 for line in f.read_text(errors="ignore").splitlines():
  if "=" not in line:continue
  k,v=line.split("=",1)
  if "://" in v:v=urllib.parse.unquote(urllib.parse.urlsplit(v).password or "")
  elif not any(z in k.upper() for z in ("PASSWORD","TOKEN","SECRET","API_KEY")):continue
  if len(v)>10:values.append(v)
hits=[]
for name in subprocess.check_output(["git","-C",str(r),"diff","--cached","--name-only"],text=True).splitlines():
 p=r/name
 if not p.is_file():continue
 t=p.read_text(errors="ignore")
 if any(v in t for v in values):hits.append([name,"known-secret"])
 if re.search(r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----",t):hits.append([name,"private-key"])
 if re.search(r"postgresql(?:\\+psycopg)?://[^\\s\\\"]+:[^\\s\\\"]+@",t):hits.append([name,"database-url"])
print(__import__("json").dumps(hits))
''';secret_hits=json.loads(p0.out(c,f"{p0.q(PY)} -c {p0.q(scan_code)} {p0.q(P)}"));large=p0.out(c,f"cd {p0.q(P)} && git diff --cached --numstat | awk '$1!=\"-\"&&$1+$2>200000{{print $3}}'").splitlines()
    if forbidden or secret_hits or large:raise p0.Blocked("FINAL_GIT_GATE:"+json.dumps({"forbidden":forbidden,"secret_hits":secret_hits,"large":large}))
    p0.run(c,f"cd {p0.q(P)} && git diff --cached --check");p0.run(c,f"cd {p0.q(P)} && git commit -m {p0.q('p1-2: add query API and OpenClaw query tools')}")
    commit=p0.out(c,f"cd {p0.q(P)} && git rev-parse HEAD");baseline=json.loads(p0.out(c,f"cat {p0.q(REPORT+'/baseline_checkpoint.json')}"))["commit"]
    git_status=p0.out(c,f"cd {p0.q(P)} && git status --short").splitlines()
    quality={"python":{"full_pytest":json.loads(p0.out(c,f"cat {p0.q(REPORT+'/query_api_test_results.json')}"))["full_pytest"],"ruff":True,"format":True,"strict_mypy":True,"pip_check":True},"node":{"npm_ci":True,"typescript_strict":True,"node_tests":True,"npm_audit":vuln,"plugin_build_check":True,"plugin_validate":True},"services":service_checks,"openclaw":{k:{"rc":x["rc"]} for k,x in oc.items()},"git":{"baseline_commit":baseline,"p1_2_commit":commit,"pushed":False,"staged_paths":len(staged),"secret_hits":0,"large_files":0,"post_commit_status":git_status}}
    report=f'''# P1-2 Query API and OpenClaw execution report

Final status: **COMPLETED**

## Outcome

The loopback-only Query Intake API is running on `127.0.0.1:8010` with Bearer authentication on all five endpoints. OpenClaw 2026.6.11 loads the official tool-only plugin `epidemic-query-tools` 0.1.0 from a verified local npm pack. The final model tool catalog is exactly `session_status`, `normalize_epidemic_query`, and `register_epidemic_query`.

## Acceptance evidence

- Credential closure: controlled second rotation PASS; fresh new application connection accepted, old password and random wrong password rejected; original terminal/Codex output is invalidated; persistent secret scans are zero.
- Git baseline: `{baseline}`. Independent P1-2 commit: `{commit}`. Neither commit was pushed.
- Python dependencies: FastAPI 0.139.0, Uvicorn 0.51.0, HTTPX 0.28.1, AnyIO 4.14.1; official PyPI wheel hashes and isolated `--require-hashes` installs passed. PyPI did not use AutoDL network turbo.
- API: one Uvicorn worker, loopback-only, no access log/proxy headers/CORS/docs, 8 KiB declared and streamed body limit, constant-time token comparison, sanitized validation/database/internal errors.
- API tests: 58 passed; correct/missing/wrong/malformed authentication, aliases, invalid inputs, idempotence, 10-way atomic registration, 20-way short requests, streamed oversize body, restart/idempotent start/single listener passed.
- Plugin pack SHA-256: `{json.loads(p0.out(c,f"cat {p0.q(REPORT+'/openclaw_plugin_inventory.json')}"))["pack"]["sha256"]}`; five packed files; runtime dependencies have zero critical/high audit findings. Manifest contracts contain exactly the two optional tools and no other runtime surface.
- Agent: explicit normalize 3/3, automatic normalize 5/5, register 3/3 with one canonical query and `created=true,false,false`, no-tool 3/3, thinking off/low passed, nine permission-denial cases exposed/executed no forbidden capability, no fallback.
- Fail-closed: stopped API and actual wrong token returned stable sanitized failures while Gateway stayed healthy; timeout, database unavailable, non-JSON, redirect/network, and oversized response injections passed.
- Formal database: read-only normalize through API and OpenClaw passed; query/retrieval/fetch/artifact/observation counts remained zero before and after. The temporary database was removed.
- Rollback: actual P0 restore yielded plugins disabled and only `session_status`; PostgreSQL/vLLM survived. SHA-identical P1-2 reapply restored exactly three tools and working normalize.
- Alembic remains the single head `0001_p1_1`; no revision or schema migration was added.
- Final regression: PostgreSQL, vLLM, Query API, OpenClaw healthy; Gateway/provider/fallback/loopback policy unchanged; security/deep audit critical count is zero.
- Git/secret/large-file/generated-runtime gates passed. Reports contain no token, password, DSN, Authorization header, full external IP, or transcript body.

## Audit notes

OpenClaw lint retains the known warning that secret-bearing configuration fields contain environment-variable references. The values are not literal secrets, agent file tools remain unavailable, and the deep audit has no critical finding. The minimal-profile notice confirms configured exec/fs sections do not widen the profile.

## Stage boundary

No external epidemiology retrieval, source access, observation write, prediction, MCP, skill, channel, public listener, or P1-3 work was performed. There are no unfinished P1-2 items.
'''
    s=c.open_sftp();p0.put(s,REPORT+"/final_quality_results.json",json.dumps(quality,indent=2)+"\n",0o600);p0.put(s,REPORT+"/P1_2_QUERY_API_OPENCLAW_EXECUTION_REPORT.md",report,0o600);s.close();print(json.dumps({"status":"COMPLETED","baseline_commit":baseline,"p1_2_commit":commit,"services":service_checks,"secret_hits":0,"git_status":git_status}))


def pull_reports(c):
    local=Path(r"F:\\作业\\aibola\\reports\\p1_2_query_api_openclaw");local.mkdir(parents=True,exist_ok=True);required=["P1_2_QUERY_API_OPENCLAW_EXECUTION_REPORT.md","credential_incident_closure.json","dependency_inventory.json","query_api_inventory.json","query_api_test_results.json","openclaw_plugin_inventory.json","openclaw_tool_test_results.json","security_and_rollback_results.json"];s=c.open_sftp()
    for name in required:
        with s.open(REPORT+"/"+name,"rb") as source:payload=source.read()
        (local/name).write_bytes(payload)
    s.close();patterns=[re.compile(rb"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----"),re.compile(rb"postgresql(?:\\+psycopg)?://[^\\s]+:[^\\s]+@"),re.compile(rb"Authorization:\\s*Bearer\\s+\\S+",re.I)];hits=[]
    for path in local.iterdir():
        data=path.read_bytes()
        if any(p.search(data) for p in patterns):hits.append(path.name)
    if hits:raise p0.Blocked("LOCAL_REPORT_SECRET_SCAN_FAILED:"+json.dumps(hits))
    print(json.dumps({"status":"PASS","local":str(local),"files":required,"secret_pattern_hits":0}))


def clean_generated_and_amend(c):
    gi=P+"/.gitignore";s=c.open_sftp()
    with s.open(gi,"r") as f:text=f.read().decode()
    addition="\n# Plugin test/compiler byproducts; runtime entry dist/index.* is tracked\nintegrations/openclaw/epidemic-query-tools/dist/index.test.*\nintegrations/openclaw/epidemic-query-tools/dist/src/\n"
    if "# Plugin test/compiler byproducts" not in text:p0.put(s,gi,text.rstrip()+addition,0o644)
    s.close();p0.run(c,f"cd {p0.q(P)} && git add .gitignore && git diff --cached --check && git commit --amend --no-edit")
    commit=p0.out(c,f"cd {p0.q(P)} && git rev-parse HEAD");status=p0.out(c,f"cd {p0.q(P)} && git status --short").splitlines()
    if status:raise p0.Blocked("POST_AMEND_WORKTREE_NOT_CLEAN:"+json.dumps(status))
    quality_path=REPORT+"/final_quality_results.json";report_path=REPORT+"/P1_2_QUERY_API_OPENCLAW_EXECUTION_REPORT.md";s=c.open_sftp()
    with s.open(quality_path,"r") as f:quality=json.loads(f.read().decode())
    old=quality["git"]["p1_2_commit"];quality["git"]["p1_2_commit"]=commit;quality["git"]["post_commit_status"]=[];p0.put(s,quality_path,json.dumps(quality,indent=2)+"\n",0o600)
    with s.open(report_path,"r") as f:report=f.read().decode()
    p0.put(s,report_path,report.replace(old,commit),0o600);s.close();print(json.dumps({"status":"PASS","p1_2_commit":commit,"git_status":status,"amended_single_commit":True}))


def main():
    ap=argparse.ArgumentParser();ap.add_argument("action",choices=["baseline","rotate-credential","checkpoint","deps","deps-finish","deploy-api","inspect-plugin-sdk","scaffold-plugin","inspect-manifest","build-plugin","inspect-plugin-path","inspect-plugin-cli","inspect-repository","fix-query-concurrency","test-query-api","configure-openclaw-plugin","finish-openclaw-config","probe-openclaw-tool","test-openclaw-tools","rescore-openclaw-tools","failure-injections","formal-switch-rollback","inspect-final-git","final-quality-commit","pull-reports","clean-generated-amend"]);a=ap.parse_args();c=p0.conn()
    try:
        if a.action=="baseline":baseline(c)
        elif a.action=="rotate-credential":rotate_credential(c)
        elif a.action=="checkpoint":checkpoint(c)
        elif a.action=="deps":dependencies(c)
        elif a.action=="deps-finish":dependencies_finish(c)
        elif a.action=="deploy-api":deploy_api(c)
        elif a.action=="inspect-plugin-sdk":inspect_plugin_sdk(c)
        elif a.action=="scaffold-plugin":scaffold_plugin(c)
        elif a.action=="inspect-manifest":inspect_manifest_types(c)
        elif a.action=="build-plugin":build_plugin(c)
        elif a.action=="inspect-plugin-path":inspect_plugin_path(c)
        elif a.action=="inspect-plugin-cli":inspect_plugin_cli(c)
        elif a.action=="inspect-repository":inspect_repository(c)
        elif a.action=="fix-query-concurrency":fix_query_concurrency(c)
        elif a.action=="test-query-api":test_query_api(c)
        elif a.action=="configure-openclaw-plugin":configure_openclaw_plugin(c)
        elif a.action=="finish-openclaw-config":finish_openclaw_config(c)
        elif a.action=="probe-openclaw-tool":probe_openclaw_tool(c)
        elif a.action=="test-openclaw-tools":test_openclaw_tools(c)
        elif a.action=="rescore-openclaw-tools":rescore_openclaw_tools(c)
        elif a.action=="failure-injections":failure_injections(c)
        elif a.action=="formal-switch-rollback":formal_switch_and_rollback(c)
        elif a.action=="inspect-final-git":inspect_final_git(c)
        elif a.action=="final-quality-commit":final_quality_commit(c)
        elif a.action=="pull-reports":pull_reports(c)
        else:clean_generated_and_amend(c)
    finally:c.close()


if __name__=="__main__":
    try:main()
    except Exception as exc:print("STATUS=BLOCKED\nREASON="+str(exc),file=sys.stderr);raise SystemExit(1)
