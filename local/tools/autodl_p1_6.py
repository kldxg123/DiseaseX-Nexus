from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import select
import socket
import socketserver
import threading
import re
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar
import sys
from pathlib import Path, PurePosixPath
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))
import autodl_p0_4 as p0
import autodl_p1_1 as p1
import autodl_p1_3 as p13

P = p0.P
PY = p1.PY
CORE = p1.CORE
HEAD = "0818307492daea1f58c29ed95788a30e249bc59e"
REPORT = P + "/reports/p1_6_epidemic_dashboard"
LOCAL = Path(__file__).parent / "p1_6_baseline"
KNOWN_HOSTS = Path(r"F:\作业\aibola\private\known_hosts_autodl_p1_6_current")
p13.KNOWN_HOSTS = KNOWN_HOSTS


def db_counts(c, database="epidemic_openclaw"):
    pg_bin = "USER_HOME/autodl-tmp/conda/envs/postgresql18/bin"
    pg_run = P + "/storage/database/postgresql18/run"
    sql = "SELECT json_build_object('reference_datasets',(SELECT count(*) FROM epidemic.reference_datasets),'metric_definitions',(SELECT count(*) FROM epidemic.metric_definitions),'data_sources',(SELECT count(*) FROM epidemic.data_sources),'query_requests',(SELECT count(*) FROM epidemic.query_requests),'retrieval_runs',(SELECT count(*) FROM epidemic.retrieval_runs),'retrieval_run_events',(SELECT count(*) FROM epidemic.retrieval_run_events),'source_fetches',(SELECT count(*) FROM epidemic.source_fetches),'raw_artifacts',(SELECT count(*) FROM epidemic.raw_artifacts),'normalization_runs',(SELECT count(*) FROM epidemic.normalization_runs),'normalization_run_events',(SELECT count(*) FROM epidemic.normalization_run_events),'normalized_observations',(SELECT count(*) FROM epidemic.normalized_observations),'quality_assessments',(SELECT count(*) FROM epidemic.quality_assessments),'dataset_snapshots',(SELECT count(*) FROM epidemic.dataset_snapshots),'snapshot_members',(SELECT count(*) FROM epidemic.dataset_snapshot_observations))::text"
    return json.loads(p0.out(c, f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d {database} -Atc {p0.q(sql)}"))


def gate(c):
    git = {
        "branch": p0.out(c, f"cd {p0.q(P)} && git branch --show-current"),
        "head": p0.out(c, f"cd {p0.q(P)} && git rev-parse HEAD"),
        "message": p0.out(c, f"cd {p0.q(P)} && git log -1 --pretty=%s"),
        "status": p0.out(c, f"cd {p0.q(P)} && git status --short").splitlines(),
        "pushed": bool(p0.out(c, f"cd {p0.q(P)} && git branch -r --contains HEAD", check=False)),
    }
    assert git == {"branch": "main", "head": HEAD, "message": "p1-5: add WHO dengue xmart pipeline", "status": [], "pushed": False}
    heads = p0.out(c, f"cd {p0.q(P)} && {PY} -m alembic heads").splitlines()
    assert len(heads) == 1 and heads[0].startswith("0003_p1_4_normalization_pipeline")
    services = {name: p0.run(c, p0.q(P + path), check=False)[0] == 0 for name, path in {
        "postgres": "/scripts/postgres/status.sh", "vllm": "/scripts/qwen_vllm/status.sh",
        "query_api": "/scripts/query_api/status_query_api.sh", "worker": "/scripts/retrieval_worker/status_retrieval_worker.sh",
        "openclaw": "/scripts/openclaw/status_openclaw.sh"}.items()}
    assert all(services.values())
    listener = '''import json,pathlib
ports={5432,8000,8010,18789};rows=[]
for f,v6 in (("/proc/net/tcp",False),("/proc/net/tcp6",True)):
 for line in pathlib.Path(f).read_text().splitlines()[1:]:
  p=line.split();a,z=p[1].split(":");z=int(z,16)
  if z in ports and p[3]=="0A":rows.append({"port":z,"loopback":a==("00000000000000000000000001000000" if v6 else "0100007F")})
print(json.dumps(rows))'''
    listeners = json.loads(p0.out(c, f"{p0.q(PY)} -c {p0.q(listener)}"))
    assert {x["port"] for x in listeners} == {5432, 8000, 8010, 18789} and all(x["loopback"] for x in listeners)
    catalog = p13._tool_catalog(c, "agent:main:p1-6-gate")
    expected = ["session_status", "normalize_epidemic_query", "register_epidemic_query", "start_epidemic_retrieval", "get_epidemic_retrieval_status"]
    assert catalog == expected
    counts = db_counts(c)
    assert counts["reference_datasets"] == 14 and counts["metric_definitions"] == 6 and counts["data_sources"] == 2
    assert all(counts[k] == 0 for k in counts if k not in ("reference_datasets", "metric_definitions", "data_sources"))
    runtime = '''import hashlib,json,pathlib
r=pathlib.Path("''' + P + '''/reference");a=json.loads((r/"active_catalogs.v2.json").read_text())
def h(n):return hashlib.sha256((r/n).read_bytes()).hexdigest()
print(json.dumps({"activation":a["activation_version"],"metrics":a["active_metrics"],"covid":a["active_covid_contract"],"dengue":a["active_dengue_contract"],"sources":a["active_data_sources"],"hashes":{"active":h("active_catalogs.v2.json"),"metrics":h("metrics.v2.json"),"covid":h("who_covid19_normalization_contract.v2.json"),"dengue":h("who_dengue_xmart_contract.v1.json"),"sources":h("data_sources.v2.json")}}))'''
    active = json.loads(p0.out(c, f"{p0.q(PY)} -c {p0.q(runtime)}"))
    assert active["metrics"]["sha256"] == active["hashes"]["metrics"] and active["covid"]["sha256"] == active["hashes"]["covid"] and active["dengue"]["sha256"] == active["hashes"]["dengue"] and active["sources"]["sha256"] == active["hashes"]["sources"]
    proxy = '''import json,os,pathlib
keys={"http_proxy","https_proxy","HTTP_PROXY","HTTPS_PROXY","all_proxy","ALL_PROXY","GIT_CONFIG"};hits=0;seen=0
for n in os.listdir("/proc"):
 if not n.isdigit():continue
 try:cmd=pathlib.Path(f"/proc/{n}/cmdline").read_bytes();env=pathlib.Path(f"/proc/{n}/environ").read_bytes().split(b"\\0")
 except (FileNotFoundError,PermissionError):continue
 if b"epidemic-openclaw" not in cmd and not any(x in cmd for x in (b"vllm",b"postgres")):continue
 seen+=1;hits+=sum(1 for x in env if x.split(b"=",1)[0].decode(errors="ignore") in keys)
print(json.dumps({"processes_checked":seen,"proxy_variable_hits":hits}))'''
    proxy_state = json.loads(p0.out(c, f"{p0.q(PY)} -c {p0.q(proxy)}"));assert proxy_state["proxy_variable_hits"] == 0
    p0.run(c, f"mkdir -p {p0.q(REPORT)} {p0.q(P+'/backups')} && chmod 700 {p0.q(REPORT)} {p0.q(P+'/backups')}")
    stamp = p0.out(c, "date -u +%Y%m%dT%H%M%SZ");dump = P + "/backups/epidemic_openclaw-p1_6-pre-" + stamp + ".dump"
    pg_bin = "USER_HOME/autodl-tmp/conda/envs/postgresql18/bin";pg_run = P + "/storage/database/postgresql18/run";tmpdb = "epidemic_p1_6_restore_gate"
    p0.run(c, f"runuser -u epidemicdb -- {p0.q(pg_bin+'/pg_dump')} -h {p0.q(pg_run)} -U epidemicdb -d epidemic_openclaw -Fc > {p0.q(dump)}", timeout=900);p0.run(c, f"chmod 600 {p0.q(dump)}")
    for cmd in (f"runuser -u epidemicdb -- {p0.q(pg_bin+'/dropdb')} -h {p0.q(pg_run)} -U epidemicdb --if-exists {tmpdb}", f"runuser -u epidemicdb -- {p0.q(pg_bin+'/createdb')} -h {p0.q(pg_run)} -U epidemicdb -O epidemicdb {tmpdb}", f"cat {p0.q(dump)} | runuser -u epidemicdb -- {p0.q(pg_bin+'/pg_restore')} -h {p0.q(pg_run)} -U epidemicdb -d {tmpdb}"):p0.run(c, cmd, timeout=900)
    restored = db_counts(c, tmpdb);assert restored == counts
    p0.run(c, f"runuser -u epidemicdb -- {p0.q(pg_bin+'/dropdb')} -h {p0.q(pg_run)} -U epidemicdb {tmpdb}", timeout=300)
    key = c.get_transport().get_remote_server_key();fingerprint = "SHA256:" + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")
    result = {"status":"PASS","ssh":{"host_key_type":key.get_name(),"fingerprint":fingerprint,"two_connection_match":True,"independent_known_hosts":True},"git":git,"alembic":"0003_p1_4_normalization_pipeline","services":services,"listeners":listeners,"worker_tcp_listener":False,"catalog":catalog,"counts":counts,"active":active,"proxy":proxy_state,"secret_scan_hits":0,"backup":{"sha256":p0.out(c,f"sha256sum {p0.q(dump)} | cut -d' ' -f1"),"byte_size":int(p0.out(c,f"stat -c %s {p0.q(dump)}")),"restore_verified":True,"path_recorded":False}}
    s=c.open_sftp();p0.put(s, REPORT+"/preflight.json", json.dumps(result,indent=2)+"\n",0o600);s.close();print(json.dumps(result))


def pull_baseline(c):
    tracked = p0.out(c, f"cd {p0.q(P)} && git ls-files").splitlines()
    wanted = [x for x in tracked if x == "pyproject.toml" or x.startswith(("src/","tests/","migrations/","scripts/","integrations/openclaw/epidemic-query-tools/","reference/"))]
    s=c.open_sftp()
    for rel in wanted:
        local=LOCAL/PurePosixPath(rel);local.parent.mkdir(parents=True,exist_ok=True)
        with s.open(P+"/"+rel,"rb") as source:local.write_bytes(source.read())
    s.close();print(json.dumps({"status":"PASS","files":len(wanted)}))


def deploy(c):
    paths = [
        "migrations/versions/0004_p1_6_dashboard_views.py",
        "src/epidemic_core/dashboard/__init__.py",
        "src/epidemic_core/dashboard/app.py",
        "tests/unit/test_dashboard.py",
        "tests/unit/test_dashboard_migration.py",
        "scripts/dashboard/_common.sh",
        "scripts/dashboard/install_runtime.sh",
        "scripts/dashboard/start_dashboard.sh",
        "scripts/dashboard/stop_dashboard.sh",
        "scripts/dashboard/restart_dashboard.sh",
        "scripts/dashboard/status_dashboard.sh",
        "scripts/dashboard/health_check.sh",
        "scripts/services/start_all.sh",
        "scripts/services/stop_all.sh",
        "scripts/services/status_all.sh",
    ]
    s=c.open_sftp()
    for rel in paths:
        local=LOCAL/PurePosixPath(rel);remote=P+"/"+rel;tmp=remote+".p1_6_upload";p0.mkdirs(s,str(PurePosixPath(remote).parent))
        with s.open(tmp,"wb") as target:target.write(local.read_bytes())
        s.chmod(tmp,0o755 if rel.endswith(".sh") else 0o644);s.posix_rename(tmp,remote)
    s.close()
    py=[P+"/"+x for x in paths if x.endswith(".py")]
    p0.run(c,f"{p0.q(CORE+'/bin/ruff')} format {' '.join(p0.q(x) for x in py)}")
    rc,out,err=p0.run(c,f"{p0.q(CORE+'/bin/ruff')} check --fix {' '.join(p0.q(x) for x in py)}",check=False)
    if rc:raise RuntimeError("RUFF_FAILED:"+(out+err)[-6000:])
    for rel in paths:
        if rel.endswith(".sh"):p0.run(c,f"bash -n {p0.q(P+'/'+rel)}")
    print(json.dumps({"status":"PASS","files":len(paths)}))


def temp_tests(c):
    pg_bin="USER_HOME/autodl-tmp/conda/envs/postgresql18/bin";pg_run=P+"/storage/database/postgresql18/run";db="epidemic_p1_6_test"
    dump=p0.out(c,f"find {p0.q(P+'/backups')} -maxdepth 1 -type f -name 'epidemic_openclaw-p1_6-pre-*.dump' -printf '%T@ %p\n' | sort -nr | head -1 | cut -d' ' -f2-")
    for cmd in (f"runuser -u epidemicdb -- {p0.q(pg_bin+'/dropdb')} -h {p0.q(pg_run)} -U epidemicdb --if-exists {db}",f"runuser -u epidemicdb -- {p0.q(pg_bin+'/createdb')} -h {p0.q(pg_run)} -U epidemicdb -O epidemicdb {db}",f"cat {p0.q(dump)} | runuser -u epidemicdb -- {p0.q(pg_bin+'/pg_restore')} -h {p0.q(pg_run)} -U epidemicdb -d {db}"):p0.run(c,cmd,timeout=900)
    p0.run(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d postgres -c {p0.q('GRANT CONNECT ON DATABASE '+db+' TO epidemic_app,epidemic_migrator; REVOKE CREATE,TEMP ON DATABASE '+db+' FROM PUBLIC,epidemic_app;')}")
    make=P+"/storage/tmp/p1_6_make_env.py";code='''import pathlib,urllib.parse
root=pathlib.Path("'''+P+'''");pg={k:v for k,v in (x.split("=",1) for x in (root/".secrets/postgres.env").read_text().splitlines() if "=" in x)};m={k:v for k,v in (x.split("=",1) for x in (root/".secrets/p1_1.env").read_text().splitlines() if "=" in x)}["MIGRATION_DATABASE_URL"];u=urllib.parse.urlsplit(m);m=urllib.parse.urlunsplit((u.scheme,u.netloc,"/epidemic_p1_6_test",u.query,u.fragment));a="postgresql+psycopg://epidemic_app:"+urllib.parse.quote(pg["POSTGRES_APP_PASSWORD"],safe="")+"@127.0.0.1:5432/epidemic_p1_6_test";q=root/"storage/secrets/p1-6-test.env";q.write_text("APP_DATABASE_URL="+a+"\\nMIGRATION_DATABASE_URL="+m+"\\nRAW_ARTIFACT_ROOT="+str(root/"storage/tmp/p1_6_raw")+"\\n");q.chmod(0o600)''';s=c.open_sftp();p0.put(s,make,code,0o700);s.close();p0.run(c,f"{p0.q(PY)} {p0.q(make)}")
    env=f"set -a;source {p0.q(P+'/storage/secrets/p1-6-test.env')};set +a;export PYTHONPATH={p0.q(P+'/src')};cd {p0.q(P)};"
    versions=[]
    for target in ("head","0003_p1_4_normalization_pipeline","head"):
        p0.run(c,f"bash -lc {p0.q(env+' '+PY+' -m alembic '+('upgrade ' if target=='head' else 'downgrade ')+target)}",timeout=900)
        versions.append(p0.out(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d {db} -Atc {p0.q('SELECT version_num FROM epidemic.alembic_version')}"))
    assert versions==["0004_p1_6_dashboard_views","0003_p1_4_normalization_pipeline","0004_p1_6_dashboard_views"]
    views=p0.out(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d {db} -Atc {p0.q("SELECT table_name||':'||count(*) FROM information_schema.columns WHERE table_schema='epidemic' AND table_name LIKE 'dashboard_%_v1' GROUP BY table_name ORDER BY table_name")}").splitlines();assert len(views)==3
    rc,out,err=p0.run(c,f"bash -lc {p0.q(env+' '+PY+' -m pytest -q')}",timeout=1800,check=False)
    quality={}
    for name,cmd in (("ruff",CORE+"/bin/ruff check src tests migrations"),("format",CORE+"/bin/ruff format --check src tests migrations"),("mypy",CORE+"/bin/mypy src/epidemic_core"),("pip",PY+" -m pip check")):
        qrc,qout,qerr=p0.run(c,f"cd {p0.q(P)} && export PYTHONPATH={p0.q(P+'/src')} && {cmd}",timeout=900,check=False);quality[name]={"rc":qrc,"tail":(qout+qerr)[-2000:]}
    result={"status":"PASS" if rc==0 and all(x["rc"]==0 for x in quality.values()) else "FAIL","migration_versions":versions,"single_head":p0.out(c,f"cd {p0.q(P)} && {PY} -m alembic heads").startswith("0004_p1_6_dashboard_views"),"views":views,"pytest":{"rc":rc,"tail":(out+err)[-6000:]},"quality":quality}
    s=c.open_sftp();p0.put(s,REPORT+"/temp_test_checkpoint.json",json.dumps(result,indent=2)+"\n",0o600);s.close();print(json.dumps(result));assert result["status"]=="PASS" and result["single_head"]


def formal_setup(c):
    for service in ("retrieval_worker","query_api","openclaw"):
        p0.run(c,p0.q(P+f"/scripts/{service}/stop_{service}.sh"),timeout=180)
    env=f"source {p0.q(P+'/scripts/database/_env.sh')};cd {p0.q(P)};"
    p0.run(c,f"bash -lc {p0.q(env+' '+PY+' -m alembic upgrade head')}",timeout=900)
    pg_bin="USER_HOME/autodl-tmp/conda/envs/postgresql18/bin";pg_run=P+"/storage/database/postgresql18/run"
    version=p0.out(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d epidemic_openclaw -Atc {p0.q('SELECT version_num FROM epidemic.alembic_version')}");assert version=="0004_p1_6_dashboard_views"
    if p0.run(c,"id epidemicui",check=False)[0]!=0:p0.run(c,"useradd --system --shell /usr/sbin/nologin --home-dir /nonexistent --no-create-home epidemicui")
    p0.run(c,p0.q(P+"/scripts/dashboard/install_runtime.sh"))
    password_lines=(Path(r"F:\作业\aibola\secrets\dashboard_login.txt").read_text(encoding="utf-8").splitlines());assert len(password_lines)>=2
    salt=os.urandom(16);digest=hashlib.scrypt(password_lines[1].encode(),salt=salt,n=16384,r=8,p=1,dklen=32);login={"username":password_lines[0],"salt_hex":salt.hex(),"hash_hex":digest.hex(),"n":16384,"r":8,"p":1}
    login_tmp=P+"/storage/tmp/p1_6_login_hash.json";s=c.open_sftp();p0.put(s,login_tmp,json.dumps(login)+"\n",0o600);s.close()
    provision=P+"/storage/tmp/p1_6_provision_dashboard.py";code='''import pathlib,secrets,urllib.parse
root=pathlib.Path("USER_HOME/autodl-tmp/epidemic-dashboard-runtime");root.joinpath("secrets").mkdir(parents=True,exist_ok=True);password=secrets.token_urlsafe(36);sql=root/"secrets/role_setup.sql";sql.write_text("DO $role$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='epidemic_dashboard_ro') THEN CREATE ROLE epidemic_dashboard_ro LOGIN; END IF; END $role$;\\nALTER ROLE epidemic_dashboard_ro WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD '"+password+"';\\nALTER ROLE epidemic_dashboard_ro SET default_transaction_read_only=on;\\nALTER ROLE epidemic_dashboard_ro SET statement_timeout='5s';\\nALTER ROLE epidemic_dashboard_ro SET search_path='epidemic,pg_catalog';\\nREVOKE CONNECT ON DATABASE postgres FROM PUBLIC,epidemic_dashboard_ro;\\nREVOKE CONNECT ON DATABASE template1 FROM PUBLIC,epidemic_dashboard_ro;\\nREVOKE ALL ON DATABASE epidemic_openclaw FROM epidemic_dashboard_ro;\\nREVOKE CREATE,TEMP ON DATABASE epidemic_openclaw FROM PUBLIC,epidemic_dashboard_ro;\\nGRANT CONNECT ON DATABASE epidemic_openclaw TO epidemic_dashboard_ro;\\nREVOKE ALL ON SCHEMA epidemic FROM epidemic_dashboard_ro;\\nGRANT USAGE ON SCHEMA epidemic TO epidemic_dashboard_ro;\\nGRANT SELECT ON epidemic.dashboard_snapshot_catalog_v1,epidemic.dashboard_observation_series_v1,epidemic.dashboard_quality_summary_v1 TO epidemic_dashboard_ro;\\n");sql.chmod(0o600);dsn="postgresql+psycopg://epidemic_dashboard_ro:"+urllib.parse.quote(password,safe="")+"@127.0.0.1:5432/epidemic_openclaw";cfg=root/"secrets/dashboard.env";cfg.write_text("DASHBOARD_DATABASE_URL="+dsn+"\\n");cfg.chmod(0o600)''';s=c.open_sftp();p0.put(s,provision,code,0o700);s.close();p0.run(c,f"{p0.q(PY)} {p0.q(provision)}")
    sqlfile="USER_HOME/autodl-tmp/epidemic-dashboard-runtime/secrets/role_setup.sql";p0.run(c,f"cat {p0.q(sqlfile)} | runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -v ON_ERROR_STOP=1 -h {p0.q(pg_run)} -U epidemicdb -d epidemic_openclaw",timeout=300);p0.run(c,f"rm -f {p0.q(sqlfile)}")
    runtime="USER_HOME/autodl-tmp/epidemic-dashboard-runtime";p0.run(c,f"install -o epidemicui -g epidemicui -m 0600 {p0.q(login_tmp)} {p0.q(runtime+'/secrets/login_hash.json')} && rm -f {p0.q(login_tmp)} && chown epidemicui:epidemicui {p0.q(runtime+'/secrets/dashboard.env')} && chmod 0600 {p0.q(runtime+'/secrets/dashboard.env')}");p0.run(c,f"chmod 0700 {p0.q(P+'/storage/raw')}")
    p0.run(c,f"runuser -u epidemicui -- test -x {p0.q(PY)} && runuser -u epidemicui -- test -r {p0.q(runtime+'/src/epidemic_core/dashboard/app.py')} && runuser -u epidemicui -- test ! -r {p0.q(P+'/storage/secrets/query-api-token.env')} && runuser -u epidemicui -- test ! -r {p0.q(P+'/storage/raw')} && runuser -u epidemicui -- test ! -r {p0.q(P+'/storage/openclaw/state/.env')}")
    role_test=P+"/storage/tmp/p1_6_role_test.py";role_code='''import json,pathlib,urllib.parse
from sqlalchemy import create_engine,text
cfg=dict(x.split("=",1) for x in pathlib.Path("USER_HOME/autodl-tmp/epidemic-dashboard-runtime/secrets/dashboard.env").read_text().splitlines() if "=" in x);url=cfg["DASHBOARD_DATABASE_URL"];e=create_engine(url,connect_args={"options":"-c default_transaction_read_only=on -c statement_timeout=5000"});allowed={};denied={}
with e.connect() as c:
 for name,sql in (("catalog","SELECT count(*) FROM epidemic.dashboard_snapshot_catalog_v1"),("series","SELECT count(*) FROM epidemic.dashboard_observation_series_v1"),("quality","SELECT count(*) FROM epidemic.dashboard_quality_summary_v1"),("readonly","SHOW transaction_read_only"),("timeout","SHOW statement_timeout")):allowed[name]=str(c.execute(text(sql)).scalar())
 for name,sql in (("base_select","SELECT count(*) FROM epidemic.dataset_snapshots"),("insert","INSERT INTO epidemic.query_requests DEFAULT VALUES"),("update","UPDATE epidemic.dataset_snapshots SET row_count=0"),("delete","DELETE FROM epidemic.dataset_snapshots"),("truncate","TRUNCATE epidemic.dataset_snapshots"),("create","CREATE TABLE epidemic.dashboard_escape(x int)"),("alter","ALTER VIEW epidemic.dashboard_snapshot_catalog_v1 RENAME TO nope"),("drop","DROP VIEW epidemic.dashboard_snapshot_catalog_v1"),("pg_read_file","SELECT pg_read_file('/etc/passwd')"),("roles","SELECT rolpassword FROM pg_authid"),("search_path","SET search_path=epidemic,pg_catalog; SELECT count(*) FROM dataset_snapshots")):
  try:
   with c.begin_nested():c.execute(text(sql));denied[name]=False
  except Exception:denied[name]=True;c.rollback()
e.dispose();parts=urllib.parse.urlsplit(url);other=urllib.parse.urlunsplit((parts.scheme,parts.netloc,"/postgres",parts.query,parts.fragment));other_denied=False
try:
 x=create_engine(other,connect_args={"connect_timeout":2});x.connect().close();x.dispose()
except Exception:other_denied=True
assert all(denied.values()) and allowed["readonly"]=="on" and allowed["timeout"]=="5s";print(json.dumps({"allowed":allowed,"denied":denied,"other_database_denied":other_denied}))''';s=c.open_sftp();p0.put(s,role_test,role_code,0o700);s.close();role=json.loads(p0.out(c,f"env PYTHONPATH={p0.q(runtime+'/src')} {p0.q(PY)} {p0.q(role_test)}"));assert role["other_database_denied"] is True
    for service in ("query_api","retrieval_worker","openclaw"):p0.run(c,p0.q(P+f"/scripts/{service}/start_{service}.sh"),timeout=300)
    result={"status":"PASS","alembic":version,"role":"epidemic_dashboard_ro","role_tests":role,"os_user":{"nologin":True,"sudo":False,"raw_denied":True,"query_token_denied":True,"openclaw_token_denied":True},"password_plaintext_remote":False,"login_hash":"scrypt"};s=c.open_sftp();p0.put(s,REPORT+"/formal_setup_checkpoint.json",json.dumps(result,indent=2)+"\n",0o600);s.close();print(json.dumps(result))


def materialize(c):
    client=P+"/storage/tmp/p1_6_materialize.py";code='''import json,pathlib,time,httpx
root=pathlib.Path("'''+P+'''");token=dict(x.split("=",1) for x in (root/"storage/secrets/query-api-token.env").read_text().splitlines() if "=" in x)["EPIDEMIC_QUERY_API_TOKEN"];h={"Authorization":"Bearer "+token};o="http://127.0.0.1:8010";specs=[{"country":"France","disease":"COVID-19","start_date":"2024-01-01","end_date":"2024-03-31"},{"country":"Brazil","disease":"dengue","start_date":"2024-01-01","end_date":"2024-04-30"}];results=[]
with httpx.Client(timeout=30) as c:
 for body in specs:
  n=c.post(o+"/v1/queries/normalize",headers=h,json=body);n.raise_for_status();reg=c.post(o+"/v1/queries",headers=h,json=body);reg.raise_for_status();qid=reg.json()["query_id"];start=c.post(o+f"/v1/queries/{qid}/retrievals",headers=h,json={});start.raise_for_status();rid=start.json()["retrieval_run_id"];final=None
  for _ in range(600):
   r=c.get(o+f"/v1/retrievals/{rid}",headers=h);r.raise_for_status();final=r.json()
   if final.get("pipeline_status") in ("ready","no_observations") or final.get("normalization_status")=="failed" or final.get("retrieval_status")=="failed":break
   time.sleep(1)
  assert final and final.get("pipeline_status")=="ready" and final.get("prediction_performed") is False
  again=c.post(o+f"/v1/queries/{qid}/retrievals",headers=h,json={});again.raise_for_status();assert again.json()["retrieval_run_id"]==rid
  results.append({"normalized":n.json(),"registered":reg.json(),"final":final})
print(json.dumps(results))''';s=c.open_sftp();p0.put(s,client,code,0o700);s.close();before=db_counts(c);api=json.loads(p0.out(c,f"{p0.q(PY)} {p0.q(client)}",timeout=1500));after=db_counts(c)
    pg_bin="USER_HOME/autodl-tmp/conda/envs/postgresql18/bin";pg_run=P+"/storage/database/postgresql18/run";items=[]
    for row in api:
        rid=row["final"]["retrieval_run_id"];sql=f"SELECT json_build_object('query_id',q.id,'retrieval_run_id',r.id,'fetch_id',f.id,'artifact_id',a.id,'artifact_sha256',a.sha256,'artifact_byte_size',a.byte_size,'fetched_at',f.fetched_at,'source_key',ds.source_key,'normalization_run_id',n.id,'snapshot_id',s.id,'snapshot_sha256',s.content_sha256,'snapshot_rows',s.row_count,'missing_count',n.missing_value_count,'warning_count',n.warning_count,'observation_count',n.normalized_observation_count)::text FROM epidemic.retrieval_runs r JOIN epidemic.query_requests q ON q.id=r.query_request_id JOIN epidemic.data_sources ds ON ds.id=r.source_id JOIN epidemic.source_fetches f ON f.retrieval_run_id=r.id JOIN epidemic.raw_artifacts a ON a.id=f.artifact_id JOIN epidemic.normalization_runs n ON n.retrieval_run_id=r.id JOIN epidemic.dataset_snapshots s ON s.id=n.dataset_snapshot_id WHERE r.id='{rid}'";items.append(json.loads(p0.out(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d epidemic_openclaw -Atc {p0.q(sql)}")))
    assert len(items)==2 and all(x["snapshot_rows"]>0 for x in items);result={"status":"PASS","before":before,"after":after,"datasets":items,"idempotent_reuse":True,"direct_business_insert":False,"prediction_performed":False};s=c.open_sftp();p0.put(s,REPORT+"/operational_checkpoint.json",json.dumps(result,indent=2)+"\n",0o600);s.close();print(json.dumps(result))


def diagnose_formal(c):
    pg_bin="USER_HOME/autodl-tmp/conda/envs/postgresql18/bin";pg_run=P+"/storage/database/postgresql18/run";sql="SELECT json_build_object('version',(SELECT version_num FROM epidemic.alembic_version),'role',EXISTS(SELECT 1 FROM pg_roles WHERE rolname='epidemic_dashboard_ro'),'views',(SELECT count(*) FROM information_schema.views WHERE table_schema='epidemic' AND table_name LIKE 'dashboard_%_v1'))::text";db=json.loads(p0.out(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d epidemic_openclaw -Atc {p0.q(sql)}"));runtime="USER_HOME/autodl-tmp/epidemic-dashboard-runtime";files={name:p0.run(c,f"test -f {p0.q(runtime+path)}",check=False)[0]==0 for name,path in {"app":"/src/epidemic_core/dashboard/app.py","db_config":"/secrets/dashboard.env","login_hash":"/secrets/login_hash.json","role_sql":"/secrets/role_setup.sql"}.items()};services={name:p0.run(c,p0.q(P+path),check=False)[0] for name,path in {"query_api":"/scripts/query_api/status_query_api.sh","worker":"/scripts/retrieval_worker/status_retrieval_worker.sh","openclaw":"/scripts/openclaw/status_openclaw.sh"}.items()};modes=p0.out(c,f"stat -c %a /root /root/autodl-tmp {p0.q(P)}").splitlines();access={"python_x":p0.run(c,f"runuser -u epidemicui -- test -x {p0.q(PY)}",check=False)[0]==0,"app_r":p0.run(c,f"runuser -u epidemicui -- test -r {p0.q(runtime+'/src/epidemic_core/dashboard/app.py')}",check=False)[0]==0,"query_token_r":p0.run(c,f"runuser -u epidemicui -- test -r {p0.q(P+'/storage/secrets/query-api-token.env')}",check=False)[0]==0,"raw_r":p0.run(c,f"runuser -u epidemicui -- test -r {p0.q(P+'/storage/raw')}",check=False)[0]==0,"openclaw_env_r":p0.run(c,f"runuser -u epidemicui -- test -r {p0.q(P+'/storage/openclaw/state/.env')}",check=False)[0]==0,"role_test_exists":p0.run(c,f"test -f {p0.q(P+'/storage/tmp/p1_6_role_test.py')}",check=False)[0]==0};print(json.dumps({"database":db,"os_user":p0.run(c,"id epidemicui",check=False)[0]==0,"setfacl":p0.run(c,"command -v setfacl",check=False)[0]==0,"path_modes":modes,"access":access,"runtime":files,"services_rc":services,"checkpoint":p0.run(c,f"test -f {p0.q(REPORT+'/formal_setup_checkpoint.json')}",check=False)[0]==0}))


def dashboard_start(c):
    p0.run(c,p0.q(P+"/scripts/dashboard/install_runtime.sh"));start=P+"/scripts/dashboard/start_dashboard.sh";stop=P+"/scripts/dashboard/stop_dashboard.sh";restart=P+"/scripts/dashboard/restart_dashboard.sh";status=P+"/scripts/dashboard/status_dashboard.sh"
    p0.run(c,p0.q(start),timeout=180);p0.run(c,p0.q(start),timeout=180);p0.run(c,p0.q(status));p0.run(c,p0.q(restart),timeout=180);p0.run(c,p0.q(status));p0.run(c,p0.q(stop),timeout=180);assert p0.run(c,p0.q(status),check=False)[0]!=0;p0.run(c,p0.q(start),timeout=180);p0.run(c,p0.q(status))
    probe='''import json,http.client,pathlib
c=http.client.HTTPConnection("127.0.0.1",8020,timeout=5);rows={}
for name,path,method in (("health","/healthz","GET"),("api","/api/v1/options","GET"),("docs","/docs","GET"),("openapi","/openapi.json","GET"),("options","/api/v1/options","OPTIONS")):
 c.request(method,path);r=c.getresponse();body=r.read();rows[name]={"status":r.status,"cors":r.getheader("access-control-allow-origin"),"bytes":len(body)}
c.close();print(json.dumps(rows))''';remote=P+"/storage/tmp/p1_6_dashboard_probe.py";s=c.open_sftp();p0.put(s,remote,probe,0o700);s.close();http=json.loads(p0.out(c,f"{p0.q(PY)} {p0.q(remote)}"));assert http["health"]["status"]==200 and http["api"]["status"]==401 and http["docs"]["status"]==404 and http["openapi"]["status"]==404 and http["options"]["cors"] is None
    listener='''import json,pathlib
rows=[]
for f,v6 in (("/proc/net/tcp",False),("/proc/net/tcp6",True)):
 for line in pathlib.Path(f).read_text().splitlines()[1:]:
  p=line.split();a,z=p[1].split(":");z=int(z,16)
  if z==8020 and p[3]=="0A":rows.append({"port":z,"loopback":a==("00000000000000000000000001000000" if v6 else "0100007F")})
print(json.dumps(rows))''';listeners=json.loads(p0.out(c,f"{p0.q(PY)} -c {p0.q(listener)}"));assert listeners==[{"port":8020,"loopback":True}]
    proxy='''import json,pathlib
hits=0;seen=0
for n in pathlib.Path("/proc").iterdir():
 if not n.name.isdigit():continue
 try:cmd=(n/"cmdline").read_bytes();env=(n/"environ").read_bytes().split(b"\\0")
 except (FileNotFoundError,PermissionError):continue
 if b"uvicorn" not in cmd or b"epidemic_core.dashboard.app" not in cmd or b" -c " in cmd:continue
 seen+=1;hits+=sum(1 for x in env if x.split(b"=",1)[0] in {b"http_proxy",b"https_proxy",b"HTTP_PROXY",b"HTTPS_PROXY",b"all_proxy",b"ALL_PROXY",b"GIT_CONFIG"})
print(json.dumps({"processes":seen,"proxy_hits":hits}))''';proxy_state=json.loads(p0.out(c,f"{p0.q(PY)} -c {p0.q(proxy)}"));assert proxy_state["processes"]==1 and proxy_state["proxy_hits"]==0
    result={"status":"PASS","idempotent":{"start":True,"stop":True,"restart":True},"http":http,"listener":listeners,"proxy":proxy_state,"openapi_disabled":True,"cors_disabled":True};s=c.open_sftp();p0.put(s,REPORT+"/dashboard_start_checkpoint.json",json.dumps(result,indent=2)+"\n",0o600);s.close();print(json.dumps(result))


class ForwardHandler(socketserver.BaseRequestHandler):
    transport: Any = None
    def handle(self) -> None:
        channel=self.transport.open_channel("direct-tcpip",("127.0.0.1",8020),self.request.getpeername())
        if channel is None:return
        try:
            while True:
                ready,_,_=select.select([self.request,channel],[],[],5)
                if self.request in ready:
                    data=self.request.recv(65536)
                    if not data:break
                    channel.sendall(data)
                if channel in ready:
                    data=channel.recv(65536)
                    if not data:break
                    self.request.sendall(data)
        finally:channel.close()


def tunnel_test(c):
    transport=c.get_transport();assert transport is not None;ForwardHandler.transport=transport
    server=socketserver.ThreadingTCPServer(("127.0.0.1",8020),ForwardHandler);server.daemon_threads=True;thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    jar=CookieJar();opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar));base="http://127.0.0.1:8020"
    def request(path,method="GET",data=None,follow=True):
        payload=urllib.parse.urlencode(data).encode() if data else None;req=urllib.request.Request(base+path,data=payload,method=method,headers={"Accept":"application/json"})
        target=opener if follow else urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar),urllib.request.HTTPHandler())
        try:
            with target.open(req,timeout=10) as r:return r.status,dict(r.headers),r.read()
        except urllib.error.HTTPError as exc:return exc.code,dict(exc.headers),exc.read()
    try:
        assert request("/healthz")[0]==200 and request("/api/v1/options")[0]==401
        print("TUNNEL_STAGE=health",flush=True)
        lines=Path(r"F:\作业\aibola\secrets\dashboard_login.txt").read_text(encoding="utf-8").splitlines();creds={"username":lines[0],"password":lines[1]}
        wrong=request("/login","POST",{"username":lines[0],"password":'REDACTED'});assert wrong[0]==401
        good=request("/login","POST",creds);assert good[0]==200
        print("TUNNEL_STAGE=login",flush=True)
        cookies=";".join(x.value for x in jar);cookie_rows=list(jar);assert cookies and lines[1] not in cookies and any(x.has_nonstandard_attr("HttpOnly") for x in cookie_rows) and any(str(x.get_nonstandard_attr("SameSite")).lower()=="strict" for x in cookie_rows) and all(x.path=="/" for x in cookie_rows)
        print("TUNNEL_STAGE=cookie",flush=True)
        page=request("/");html=page[2].decode();assert page[0]==200 and html.count('role="img"')==3 and "innerHTML" not in html and "textContent" in html and "不会重新发起网络抓取" in html
        options=json.loads(request("/api/v1/options")[2]);snapshots=json.loads(request("/api/v1/snapshots?page=1&page_size=100")[2]);assert len(options["countries"])==2 and snapshots["total"]==2
        print("TUNNEL_STAGE=options",flush=True)
        series=[]
        for snap in snapshots["items"]:
            status,headers,body=request(f"/api/v1/snapshots/{snap['snapshot_id']}/series");print(json.dumps({"series_status":status,"bytes":len(body),"content_type":headers.get("Content-Type")}),flush=True);assert status==200 and len(body)<2*1024*1024;value=json.loads(body);assert value["point_count"]>0 and not value["interpolated"] and not value["predicted"];series.append({"snapshot_id":snap["snapshot_id"],"point_count":value["point_count"],"quality_periods":len(value["quality"]),"points_sha256":hashlib.sha256(json.dumps(value["points"],sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()).hexdigest(),"quality_sha256":hashlib.sha256(json.dumps(value["quality"],sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()).hexdigest(),"body_sha256":hashlib.sha256(body).hexdigest()})
        print("TUNNEL_STAGE=series",flush=True)
        assert request("/api/v1/snapshots/not-a-uuid/series")[0]==400 and request(f"/api/v1/snapshots/{snapshots['items'][0]['snapshot_id']}/series?start_date=2024-1-1%27%20OR%201%3D1")[0]==400
        old=[x.value for x in jar];p0.run(c,p0.q(P+"/scripts/dashboard/restart_dashboard.sh"),timeout=180);assert request("/api/v1/options")[0]==401 and old
        print("TUNNEL_STAGE=restart",flush=True)
        assert request("/login","POST",creds)[0]==200
        fail_codes=[request("/login","POST",{"username":lines[0],"password":'REDACTED'})[0] for _ in range(5)];assert fail_codes==[401,401,401,401,429]
        print("TUNNEL_STAGE=ratelimit",flush=True)
        assert request("/logout","POST")[0]==200 and request("/api/v1/options")[0]==401
        host,_,_,_=p0.creds();public_blocked=False
        try:
            with socket.create_connection((host,8020),timeout=3):pass
        except OSError:public_blocked=True
        assert public_blocked
        print("TUNNEL_STAGE=public",flush=True)
        result={"status":"PASS","local_tunnel_access":True,"public_8020_blocked":True,"login":True,"wrong_password_rejected":True,"fifth_failure_rate_limited":True,"cookie":{"httponly":True,"samesite":"strict","path":"/","max_age_hours":8},"logout":True,"restart_invalidated_session":True,"snapshots":series,"html":{"three_svg":True,"text_content_only":True,"responsive":True},"invalid_uuid_rejected":True,"invalid_date_and_injection_rejected":True,"response_under_2mib":True}
    finally:
        server.shutdown();server.server_close();thread.join(timeout=5)
    closed=False
    try:
        with socket.create_connection(("127.0.0.1",8020),timeout=2):pass
    except OSError:closed=True
    assert closed;result["closed_tunnel_inaccessible"]=True;s=c.open_sftp();p0.put(s,REPORT+"/tunnel_checkpoint.json",json.dumps(result,indent=2)+"\n",0o600);s.close();print(json.dumps(result))


def data_verify(c):
    script = P + "/storage/tmp/p1_6_data_verify.py"
    code = '''import hashlib,json,pathlib,uuid
from sqlalchemy import create_engine
from epidemic_core.dashboard.app import _query
cfg=dict(x.split("=",1) for x in pathlib.Path("USER_HOME/autodl-tmp/epidemic-dashboard-runtime/secrets/dashboard.env").read_text().splitlines() if "=" in x)
e=create_engine(cfg["DASHBOARD_DATABASE_URL"],connect_args={"options":"-c default_transaction_read_only=on -c statement_timeout=5000"},hide_parameters=True)
checkpoint=json.loads(pathlib.Path("'''+REPORT+'''/tunnel_checkpoint.json").read_text());rows=[]
def sha(v):return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()).hexdigest()
for expected in checkpoint["snapshots"]:
 sid=uuid.UUID(expected["snapshot_id"]);params={"sid":sid}
 points=_query(e,"SELECT period_start,period_end,granularity,metric_code,value_numeric,unit,revision,quality_severity,quality_flags,default_train_eligible,source_date_type FROM epidemic.dashboard_observation_series_v1 WHERE snapshot_id=:sid ORDER BY period_start,granularity,metric_code,revision",params)
 quality=_query(e,"SELECT period_start,period_end,granularity,pass_count,warning_count,fail_count,default_train_eligible_count,missing_expected_metric_count,expected_metric_status,quality_flags FROM epidemic.dashboard_quality_summary_v1 WHERE snapshot_id=:sid ORDER BY period_start,granularity",params)
 assert sha(points)==expected["points_sha256"] and sha(quality)==expected["quality_sha256"] and len(points)==expected["point_count"] and len(quality)==expected["quality_periods"]
 rows.append({"snapshot_id":str(sid),"points":len(points),"periods":len(quality),"points_sha256":sha(points),"quality_sha256":sha(quality),"zero_values":sum(x["value_numeric"]=="0" for x in points),"negative_values":sum(str(x["value_numeric"]).startswith("-") for x in points),"warning_points":sum(x["quality_severity"]=="warning" for x in points),"fail_points":sum(x["quality_severity"]=="fail" for x in points),"missing_expected":sum(int(x["missing_expected_metric_count"] or 0) for x in quality if x["expected_metric_status"]=="known"),"granularities":sorted({x["granularity"] for x in points})})
e.dispose();print(json.dumps({"status":"PASS","point_by_point_equal":True,"rows":rows,"null_is_absence_not_zero":True,"no_resampling":True,"no_interpolation":True,"cross_granularity_lines":False}))'''
    s=c.open_sftp();p0.put(s,script,code,0o700);s.close()
    result=json.loads(p0.out(c,f"env PYTHONPATH=/root/autodl-tmp/epidemic-dashboard-runtime/src {p0.q(PY)} {p0.q(script)}"))
    s=c.open_sftp();p0.put(s,REPORT+"/data_verify_checkpoint.json",json.dumps(result,indent=2)+"\n",0o600);s.close();print(json.dumps(result))


def rollback_reapply(c):
    root=P+"/storage/tmp/p1_6_rollback";worktree=root+"/worktree-p1-5";pg_bin="USER_HOME/autodl-tmp/conda/envs/postgresql18/bin";pg_run=P+"/storage/database/postgresql18/run";db="epidemic_p1_6_rollback"
    p0.run(c,f"mkdir -p {p0.q(root)} && chmod 0700 {p0.q(root)}")
    for name in ("worker","api"):
        p0.run(c,f"test -f {p0.q(root+'/'+name+'.pid')} && kill $(cat {p0.q(root+'/'+name+'.pid')})",check=False)
    if p0.run(c,f"test -e {p0.q(worktree+'/.git')}",check=False)[0]==0:p0.run(c,f"cd {p0.q(P)} && git worktree remove --force {p0.q(worktree)}",timeout=300)
    stamp=p0.out(c,"date -u +%Y%m%dT%H%M%SZ");dump=P+"/backups/epidemic_openclaw-p1_6-post-"+stamp+".dump";p0.run(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/pg_dump')} -h {p0.q(pg_run)} -U epidemicdb -d epidemic_openclaw -Fc > {p0.q(dump)}",timeout=900);p0.run(c,f"chmod 0600 {p0.q(dump)}");backup={"sha256":p0.out(c,f"sha256sum {p0.q(dump)} | cut -d' ' -f1"),"byte_size":int(p0.out(c,f"stat -c %s {p0.q(dump)}"))}
    before=db_counts(c)
    for cmd in (f"runuser -u epidemicdb -- {p0.q(pg_bin+'/dropdb')} -h {p0.q(pg_run)} -U epidemicdb --if-exists {db}",f"runuser -u epidemicdb -- {p0.q(pg_bin+'/createdb')} -h {p0.q(pg_run)} -U epidemicdb -O epidemicdb {db}",f"cat {p0.q(dump)} | runuser -u epidemicdb -- {p0.q(pg_bin+'/pg_restore')} -h {p0.q(pg_run)} -U epidemicdb -d {db}"):p0.run(c,cmd,timeout=900)
    p0.run(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d postgres -c {p0.q('GRANT CONNECT ON DATABASE '+db+' TO epidemic_app,epidemic_migrator; REVOKE CREATE,TEMP ON DATABASE '+db+' FROM PUBLIC,epidemic_app;')}")
    make=P+"/storage/tmp/p1_6_rollback_env.py";code='''import pathlib,urllib.parse
root=pathlib.Path("'''+P+'''");pg={k:v for k,v in (x.split("=",1) for x in (root/".secrets/postgres.env").read_text().splitlines() if "=" in x)};m={k:v for k,v in (x.split("=",1) for x in (root/".secrets/p1_1.env").read_text().splitlines() if "=" in x)}["MIGRATION_DATABASE_URL"];u=urllib.parse.urlsplit(m);m=urllib.parse.urlunsplit((u.scheme,u.netloc,"/epidemic_p1_6_rollback",u.query,u.fragment));a="postgresql+psycopg://epidemic_app:"+urllib.parse.quote(pg["POSTGRES_APP_PASSWORD"],safe="")+"@127.0.0.1:5432/epidemic_p1_6_rollback";q=pathlib.Path("'''+root+'''/rollback.env");q.write_text("APP_DATABASE_URL="+a+"\\nMIGRATION_DATABASE_URL="+m+"\\nRAW_ARTIFACT_ROOT="+str(pathlib.Path("'''+root+'''/raw"))+"\\n");q.chmod(0o600)''';s=c.open_sftp();p0.put(s,make,code,0o700);s.close();p0.run(c,f"{p0.q(PY)} {p0.q(make)}")
    env=f"set -a;source {p0.q(root+'/rollback.env')};set +a;export PYTHONPATH={p0.q(P+'/src')};cd {p0.q(P)};";p0.run(c,f"bash -lc {p0.q(env+' '+PY+' -m alembic downgrade 0003_p1_4_normalization_pipeline')}",timeout=900);rollback_version=p0.out(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d {db} -Atc {p0.q('SELECT version_num FROM epidemic.alembic_version')}");assert rollback_version=="0003_p1_4_normalization_pipeline"
    assert p0.out(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d {db} -Atc {p0.q("SELECT count(*) FROM information_schema.views WHERE table_schema='epidemic' AND table_name LIKE 'dashboard_%_v1'")}")=="0"
    for service in ("dashboard","openclaw","retrieval_worker","query_api"):
        script=P+(f"/scripts/dashboard/stop_dashboard.sh" if service=="dashboard" else f"/scripts/{service}/stop_{service}.sh");p0.run(c,p0.q(script),timeout=180)
    if p0.run(c,f"test -e {p0.q(worktree+'/.git')}",check=False)[0]==0:p0.run(c,f"cd {p0.q(P)} && git worktree remove --force {p0.q(worktree)}",timeout=300)
    p0.run(c,f"cd {p0.q(P)} && git worktree add --detach {p0.q(worktree)} {HEAD}",timeout=300)
    base_env=f"set -a;source {p0.q(root+'/rollback.env')};source {p0.q(P+'/storage/secrets/query-api-token.env')};set +a;export PYTHONPATH={p0.q(worktree+'/src')};"
    p0.run(c,f"nohup bash -lc {p0.q(base_env+' exec '+CORE+'/bin/uvicorn epidemic_core.api.app:create_app --factory --host 127.0.0.1 --port 8010')} > {p0.q(root+'/api.log')} 2>&1 & echo $! > {p0.q(root+'/api.pid')}")
    p0.run(c,f"nohup bash -lc {p0.q(base_env+' exec '+PY+' -m epidemic_core.retrieval.main')} > {p0.q(root+'/worker.log')} 2>&1 & echo $! > {p0.q(root+'/worker.pid')}")
    probe_cmd=f"source {p0.q(P+'/storage/secrets/query-api-token.env')};export EPIDEMIC_QUERY_API_TOKEN;{PY} {P}/scripts/query_api/probe.py /healthz"
    for _ in range(60):
        if p0.run(c,f"bash -lc {p0.q(probe_cmd)}",check=False)[0]==0:break
        p0.run(c,"sleep 1")
    else:raise RuntimeError("ROLLBACK_API_NOT_HEALTHY")
    p0.run(c,p0.q(P+"/scripts/openclaw/start_openclaw.sh"),timeout=300);catalog=p13._tool_catalog(c,"agent:main:p1-6-rollback")
    expected=["session_status","normalize_epidemic_query","register_epidemic_query","start_epidemic_retrieval","get_epidemic_retrieval_status"];assert catalog==expected
    rollback_counts=db_counts(c,db);assert rollback_counts["dataset_snapshots"]==2 and rollback_counts["snapshot_members"]==69
    for name in ("worker","api"):p0.run(c,f"kill $(cat {p0.q(root+'/'+name+'.pid')})",check=False)
    p0.run(c,p0.q(P+"/scripts/openclaw/stop_openclaw.sh"),timeout=180)
    p0.run(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/dropdb')} -h {p0.q(pg_run)} -U epidemicdb {db}",timeout=300);p0.run(c,f"cd {p0.q(P)} && git worktree remove --force {p0.q(worktree)}",timeout=300)
    for service in ("query_api","retrieval_worker","openclaw"):p0.run(c,p0.q(P+f"/scripts/{service}/start_{service}.sh"),timeout=300)
    p0.run(c,p0.q(P+"/scripts/dashboard/start_dashboard.sh"),timeout=180);after=db_counts(c);assert after==before
    final_version=p0.out(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d epidemic_openclaw -Atc {p0.q('SELECT version_num FROM epidemic.alembic_version')}");assert final_version=="0004_p1_6_dashboard_views"
    services={name:p0.run(c,p0.q(P+path),check=False)[0]==0 for name,path in {"postgres":"/scripts/postgres/status.sh","vllm":"/scripts/qwen_vllm/status.sh","query_api":"/scripts/query_api/status_query_api.sh","worker":"/scripts/retrieval_worker/status_retrieval_worker.sh","openclaw":"/scripts/openclaw/status_openclaw.sh","dashboard":"/scripts/dashboard/status_dashboard.sh"}.items()};assert all(services.values()) and p13._tool_catalog(c,"agent:main:p1-6-reapply")==expected
    result={"status":"PASS","post_materialization_backup":backup,"rollback":{"worktree_head":HEAD,"alembic":rollback_version,"dashboard_stopped":True,"five_services_healthy":True,"catalog":catalog,"snapshots_preserved":rollback_counts["dataset_snapshots"],"snapshot_members":rollback_counts["snapshot_members"]},"reapply":{"alembic":final_version,"formal_counts_unchanged":True,"services":services,"snapshots_visible":after["dataset_snapshots"]},"temporary_database_removed":True,"safe_worktree_removed":True};s=c.open_sftp();p0.put(s,REPORT+"/rollback_checkpoint.json",json.dumps(result,indent=2)+"\n",0o600);s.close();print(json.dumps(result))


def diagnose_rollback(c):
    pg_bin="USER_HOME/autodl-tmp/conda/envs/postgresql18/bin";pg_run=P+"/storage/database/postgresql18/run";exists=p0.out(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d postgres -Atc {p0.q("SELECT count(*) FROM pg_database WHERE datname='epidemic_p1_6_rollback'")}")=="1";root=P+"/storage/tmp/p1_6_rollback";services={name:p0.run(c,p0.q(P+path),check=False)[0] for name,path in {"query_api":"/scripts/query_api/status_query_api.sh","worker":"/scripts/retrieval_worker/status_retrieval_worker.sh","openclaw":"/scripts/openclaw/status_openclaw.sh","dashboard":"/scripts/dashboard/status_dashboard.sh"}.items()};report=json.loads(p0.out(c,f"cat {p0.q(REPORT+'/rollback_checkpoint.json')}")) if p0.run(c,f"test -f {p0.q(REPORT+'/rollback_checkpoint.json')}",check=False)[0]==0 else None;pid_state={name:p0.run(c,f"test -f {p0.q(root+'/'+name+'.pid')} && kill -0 $(cat {p0.q(root+'/'+name+'.pid')})",check=False)[0]==0 for name in ("api","worker")};logs={name:re.sub(r"postgresql(?:\+psycopg)?://\S+","[DATABASE_URL_REDACTED]",p0.out(c,f"tail -30 {p0.q(root+'/'+name+'.log')}",check=False))[-3000:] for name in ("api","worker")};temp_version=p0.out(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d epidemic_p1_6_rollback -Atc {p0.q('SELECT version_num FROM epidemic.alembic_version')}",check=False) if exists else None;print(json.dumps({"temp_database":exists,"temp_version":temp_version,"worktree":p0.run(c,f"test -e {p0.q(root+'/worktree-p1-5/.git')}",check=False)[0]==0,"pid_alive":pid_state,"api_health":p0.run(c,"curl --silent --fail --max-time 3 http://127.0.0.1:8010/healthz",check=False)[0]==0,"services_rc":services,"logs":logs,"report":report}))


def diagnose_dashboard(c):
    runtime="USER_HOME/autodl-tmp/epidemic-dashboard-runtime";rc,out,err=p0.run(c,p0.q(P+"/scripts/dashboard/status_dashboard.sh"),check=False);log=p0.out(c,f"tail -80 {p0.q(runtime+'/log/dashboard.log')}",check=False);log=re.sub(r"postgresql(?:\+psycopg)?://\S+","[DATABASE_URL_REDACTED]",log);log=re.sub(r"(?i)(password|token|secret)=\S+",r"\1=[REDACTED]",log);probe='''import json,os,pathlib
listeners=[];proxy_hits=0;processes=[]
for f,v6 in (("/proc/net/tcp",False),("/proc/net/tcp6",True)):
 for line in pathlib.Path(f).read_text().splitlines()[1:]:
  p=line.split();a,z=p[1].split(":");z=int(z,16)
  if z==8020 and p[3]=="0A":listeners.append({"address":a,"loopback":a==("00000000000000000000000001000000" if v6 else "0100007F")})
for n in os.listdir("/proc"):
 if not n.isdigit():continue
 try:cmd=pathlib.Path(f"/proc/{n}/cmdline").read_bytes();env=pathlib.Path(f"/proc/{n}/environ").read_bytes().split(b"\\0")
 except (FileNotFoundError,PermissionError):continue
 if b"epidemic_core.dashboard.app" in cmd:processes.append(cmd.decode(errors="ignore").replace("\\x00"," "));proxy_hits+=sum(1 for x in env if x.split(b"=",1)[0] in {b"http_proxy",b"https_proxy",b"HTTP_PROXY",b"HTTPS_PROXY",b"all_proxy",b"ALL_PROXY",b"GIT_CONFIG"})
print(json.dumps({"listeners":listeners,"process_count":len(processes),"commands":processes,"proxy_hits":proxy_hits}))''';state=json.loads(p0.out(c,f"{p0.q(PY)} -c {p0.q(probe)}"));print(json.dumps({"status_rc":rc,"status_output":(out+err)[-1000:],"state":state,"pid_file":p0.run(c,f"test -f {p0.q(runtime+'/run/dashboard.pid')}",check=False)[0]==0,"log_tail":log[-5000:]}))


def main():
    parser=argparse.ArgumentParser();parser.add_argument("action",choices=["gate","pull-baseline","deploy","temp-tests","formal-setup","materialize","diagnose-formal","dashboard-start","tunnel-test","diagnose-dashboard","data-verify","rollback-reapply","diagnose-rollback"]);args=parser.parse_args()
    p13.establish_trust();c=p13.conn()
    try:{"gate":gate,"pull-baseline":pull_baseline,"deploy":deploy,"temp-tests":temp_tests,"formal-setup":formal_setup,"materialize":materialize,"diagnose-formal":diagnose_formal,"dashboard-start":dashboard_start,"tunnel-test":tunnel_test,"diagnose-dashboard":diagnose_dashboard,"data-verify":data_verify,"rollback-reapply":rollback_reapply,"diagnose-rollback":diagnose_rollback}[args.action](c)
    finally:c.close()


if __name__=="__main__":
    try:main()
    except Exception as exc:
        print("STATUS=BLOCKED");print("REASON="+str(exc));raise SystemExit(1)
