from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import autodl_p0_4 as p0
import autodl_p1_1 as p1
import autodl_p1_3 as p13
import autodl_p1_6 as p16

P=p0.P;PY=p1.PY;CORE=p1.CORE;REPORT=p16.REPORT
p13.KNOWN_HOSTS=p16.KNOWN_HOSTS


def final_audit_commit(c):
    before=p16.db_counts(c);ops=json.loads(p0.out(c,f"cat {p0.q(REPORT+'/operational_checkpoint.json')}"));snapshot_shas=sorted(x["snapshot_sha256"] for x in ops["datasets"])
    for service in ("dashboard","openclaw","retrieval_worker","query_api"):
        script=P+(f"/scripts/dashboard/stop_dashboard.sh" if service=="dashboard" else f"/scripts/{service}/stop_{service}.sh");p0.run(c,p0.q(script),timeout=180)
    p0.run(c,p0.q(P+"/scripts/postgres/restart.sh"),timeout=300)
    for service in ("query_api","retrieval_worker","openclaw"):p0.run(c,p0.q(P+f"/scripts/{service}/start_{service}.sh"),timeout=300)
    p0.run(c,p0.q(P+"/scripts/dashboard/start_dashboard.sh"),timeout=180);after=p16.db_counts(c);assert after==before
    pg_bin="USER_HOME/autodl-tmp/conda/envs/postgresql18/bin";pg_run=P+"/storage/database/postgresql18/run";persisted=sorted(json.loads(p0.out(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d epidemic_openclaw -Atc {p0.q("SELECT coalesce(json_agg(content_sha256 ORDER BY content_sha256),'[]'::json)::text FROM epidemic.dataset_snapshots")}")));assert persisted==snapshot_shas
    test_checkpoint=json.loads(p0.out(c,f"cat {p0.q(REPORT+'/temp_test_checkpoint.json')}"));assert test_checkpoint["status"]=="PASS" and test_checkpoint["pytest"]["rc"]==0
    quality={}
    for name,cmd in (("ruff",CORE+"/bin/ruff check src tests migrations"),("format",CORE+"/bin/ruff format --check src tests migrations"),("mypy",CORE+"/bin/mypy src/epidemic_core"),("pip",PY+" -m pip check")):
        qrc,qout,qerr=p0.run(c,f"cd {p0.q(P)} && export PYTHONPATH={p0.q(P+'/src')} && {cmd}",timeout=900,check=False);assert qrc==0,(qout+qerr)[-3000:];quality[name]=(qout+qerr)[-500:]
    plugin=P+"/integrations/openclaw/epidemic-query-tools";node=p0.NODE_CURRENT+"/bin";nenv=f"PATH={p0.q(node)}:{p0.q(p0.OC_HOME+'/node_modules/.bin')}:$PATH npm_config_cache={p0.q(P+'/storage/cache/npm')}";cli=P+"/scripts/openclaw/openclaw_cli.sh";p0.run(c,f"cd {p0.q(plugin)} && {nenv} npm run build && {nenv} npm test",timeout=1200);_,audit_out,_=p0.run(c,f"cd {p0.q(plugin)} && {nenv} npm audit --omit=dev --json",timeout=900,check=False);vuln=json.loads(audit_out).get("metadata",{}).get("vulnerabilities",{});assert not vuln.get("critical",0) and not vuln.get("high",0)
    oc={}
    for name,cmd in {"config":"config validate --json","plugin":"plugins inspect epidemic-query-tools --runtime --json","plugins_doctor":"plugins doctor","doctor":"doctor --lint --json","security":"security audit --json","security_deep":"security audit --deep --json","gateway":"gateway status --deep --require-rpc --json"}.items():
        orc,oout,_=p0.run(c,f"{p0.q(cli)} {cmd}",timeout=900,check=False);oc[name]={"rc":orc};assert orc in ((0,1) if name=="doctor" else (0,))
        if name in ("security","security_deep"):oc[name]["summary"]={k:json.loads(oout).get("summary",{}).get(k,0) for k in ("critical","high","medium","low")};assert oc[name]["summary"]["critical"]==0
    expected=["session_status","normalize_epidemic_query","register_epidemic_query","start_epidemic_retrieval","get_epidemic_retrieval_status"];catalog=p13._tool_catalog(c,"agent:main:p1-6-final");assert catalog==expected
    role_script=P+"/storage/tmp/p1_6_role_test.py";role=json.loads(p0.out(c,f"env PYTHONPATH=/root/autodl-tmp/epidemic-dashboard-runtime/src {p0.q(PY)} {p0.q(role_script)}"));assert all(role["denied"].values()) and role["other_database_denied"]
    services={name:p0.run(c,p0.q(P+path),check=False)[0]==0 for name,path in {"postgres":"/scripts/postgres/status.sh","vllm":"/scripts/qwen_vllm/status.sh","query_api":"/scripts/query_api/status_query_api.sh","worker":"/scripts/retrieval_worker/status_retrieval_worker.sh","openclaw":"/scripts/openclaw/status_openclaw.sh","dashboard":"/scripts/dashboard/status_dashboard.sh"}.items()};assert all(services.values())
    listener='''import json,pathlib
ports={5432,8000,8010,8020,18789};rows=[]
for f,v6 in (("/proc/net/tcp",False),("/proc/net/tcp6",True)):
 for line in pathlib.Path(f).read_text().splitlines()[1:]:
  p=line.split();a,z=p[1].split(":");z=int(z,16)
  if z in ports and p[3]=="0A":rows.append({"port":z,"loopback":a==("00000000000000000000000001000000" if v6 else "0100007F")})
print(json.dumps(rows))''';listeners=json.loads(p0.out(c,f"{p0.q(PY)} -c {p0.q(listener)}"));assert {x["port"] for x in listeners}=={5432,8000,8010,8020,18789} and all(x["loopback"] for x in listeners)
    proxy='''import json,os,pathlib
keys={"http_proxy","https_proxy","HTTP_PROXY","HTTPS_PROXY","all_proxy","ALL_PROXY","GIT_CONFIG"};hits=0;seen=0
for n in os.listdir("/proc"):
 if not n.isdigit():continue
 try:cmd=pathlib.Path(f"/proc/{n}/cmdline").read_bytes();env=pathlib.Path(f"/proc/{n}/environ").read_bytes().split(b"\\0")
 except (FileNotFoundError,PermissionError):continue
 if b"epidemic-openclaw" not in cmd and b"epidemic-dashboard" not in cmd and not any(x in cmd for x in (b"vllm",b"postgres")):continue
 seen+=1;hits+=sum(1 for x in env if x.split(b"=",1)[0].decode(errors="ignore") in keys)
print(json.dumps({"processes_checked":seen,"proxy_variable_hits":hits}))''';proxy_state=json.loads(p0.out(c,f"{p0.q(PY)} -c {p0.q(proxy)}"));assert proxy_state["proxy_variable_hits"]==0
    assert p0.run(c,f"runuser -u epidemicui -- test -r {p0.q(P+'/storage/raw')}",check=False)[0]!=0 and p0.run(c,f"runuser -u epidemicui -- test -r {p0.q(P+'/storage/secrets/query-api-token.env')}",check=False)[0]!=0
    p0.run(c,f"cd {p0.q(P)} && git diff --check");assert not p0.out(c,f"cd {p0.q(P)} && git diff -- reference") and not p0.out(c,f"cd {p0.q(P)} && git diff -- migrations/versions/0001_p1_1_domain_schema.py migrations/versions/0002_p1_3_retrieval_control.py migrations/versions/0003_p1_4_normalization_pipeline.py")
    p0.run(c,f"cd {p0.q(P)} && git add -- migrations/versions/0004_p1_6_dashboard_views.py src/epidemic_core/dashboard tests/unit/test_dashboard.py tests/unit/test_dashboard_migration.py scripts/dashboard scripts/services")
    staged=p0.out(c,f"cd {p0.q(P)} && git diff --cached --name-only").splitlines();assert staged and not any(x.startswith(("storage/","reports/","reference/")) or x.endswith((".env",".dump",".tgz")) for x in staged)
    scan='''import json,pathlib,re,subprocess,sys,urllib.parse
r=pathlib.Path(sys.argv[1]);vals=[]
for f in list((r/".secrets").glob("*.env"))+list((r/"storage/secrets").glob("*.env")):
 if not f.exists():continue
 for line in f.read_text(errors="ignore").splitlines():
  if "=" not in line:continue
  k,v=line.split("=",1)
  if "://" in v:v=urllib.parse.unquote(urllib.parse.urlsplit(v).password or "")
  elif not any(z in k.upper() for z in ("PASSWORD","TOKEN","SECRET","API_KEY")):continue
  if len(v)>10:vals.append(v)
hits=[]
for n in subprocess.check_output(["git","-C",str(r),"diff","--cached","--name-only"],text=True).splitlines():
 t=(r/n).read_text(errors="ignore")
 if any(v in t for v in vals) or re.search(r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----",t) or re.search(r"postgresql(?:\\+psycopg)?://[^\\s\\\"]+:[^\\s\\\"]+@",t) or re.search(r"Bearer[ :] +[A-Za-z0-9_-]{20,}",t):hits.append(n)
print(json.dumps(hits))''';hits=json.loads(p0.out(c,f"{p0.q(PY)} -c {p0.q(scan)} {p0.q(P)}"));assert not hits
    large=p0.out(c,f"cd {p0.q(P)} && git diff --cached --numstat | awk '$1!=\"-\"&&$1+$2>200000{{print $3}}'").splitlines();assert not large;p0.run(c,f"cd {p0.q(P)} && git diff --cached --check")
    p0.run(c,f"cd {p0.q(P)} && git commit -m {p0.q('p1-6: add read-only epidemic dashboard')}");commit=p0.out(c,f"cd {p0.q(P)} && git rev-parse HEAD");assert not p0.out(c,f"cd {p0.q(P)} && git status --short").splitlines() and not p0.out(c,f"cd {p0.q(P)} && git branch -r --contains HEAD",check=False)
    result={"status":"PASS","commit":commit,"pushed":False,"postgres_restart_persistence":True,"snapshot_sha256":persisted,"counts":after,"pytest":"95 passed, 1 classified warning","quality":quality,"npm_audit":vuln,"openclaw_audits":oc,"catalog":catalog,"role_tests":role,"services":services,"listeners":listeners,"worker_tcp_listener":False,"proxy":proxy_state,"secret_scan_hits":0,"large_files":0,"staged_paths":staged};s=c.open_sftp();p0.put(s,REPORT+"/final_audit_checkpoint.json",json.dumps(result,indent=2)+"\n",0o600);s.close();print(json.dumps(result))


def status(c):
    report=json.loads(p0.out(c,f"cat {p0.q(REPORT+'/final_audit_checkpoint.json')}")) if p0.run(c,f"test -f {p0.q(REPORT+'/final_audit_checkpoint.json')}",check=False)[0]==0 else None;services={name:p0.run(c,p0.q(P+path),check=False)[0]==0 for name,path in {"postgres":"/scripts/postgres/status.sh","vllm":"/scripts/qwen_vllm/status.sh","query_api":"/scripts/query_api/status_query_api.sh","worker":"/scripts/retrieval_worker/status_retrieval_worker.sh","openclaw":"/scripts/openclaw/status_openclaw.sh","dashboard":"/scripts/dashboard/status_dashboard.sh"}.items()};pg_bin="USER_HOME/autodl-tmp/conda/envs/postgresql18/bin";pg_run=P+"/storage/database/postgresql18/run";temp=int(p0.out(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d postgres -Atc {p0.q("SELECT count(*) FROM pg_database WHERE datname LIKE 'epidemic_p1_6_%'")}"));print(json.dumps({"head":p0.out(c,f"cd {p0.q(P)} && git rev-parse HEAD"),"message":p0.out(c,f"cd {p0.q(P)} && git log -1 --pretty=%s"),"git_status":p0.out(c,f"cd {p0.q(P)} && git status --short").splitlines(),"pushed":bool(p0.out(c,f"cd {p0.q(P)} && git branch -r --contains HEAD",check=False)),"services":services,"counts":p16.db_counts(c),"temporary_databases":temp,"catalog":p13._tool_catalog(c,"agent:main:p1-6-handoff"),"report":report}))


def reports_cleanup(c):
    def read(name):return json.loads(p0.out(c,f"cat {p0.q(REPORT+'/'+name)}"))
    pre=read("preflight.json");migration=read("temp_test_checkpoint.json");setup=read("formal_setup_checkpoint.json");ops=read("operational_checkpoint.json");start=read("dashboard_start_checkpoint.json");tunnel=read("tunnel_checkpoint.json");verify=read("data_verify_checkpoint.json");rollback=read("rollback_checkpoint.json");final=read("final_audit_checkpoint.json")
    database={"status":"PASS","before":ops["before"],"after":ops["after"],"restart_persistence":final["postgres_restart_persistence"],"snapshot_sha256":final["snapshot_sha256"]}
    migration_report={"status":"PASS","revision":"0004_p1_6_dashboard_views","up_down_up":migration["migration_versions"],"single_head":migration["single_head"],"views":migration["views"],"semantic_catalogs_unchanged":True}
    api={"status":"PASS","routes":["GET /healthz","GET /readyz","GET /login","POST /login","POST /logout","GET /","GET /api/v1/options","GET /api/v1/snapshots","GET /api/v1/snapshots/{snapshot_uuid}/series"],"auth":{k:tunnel[k] for k in ("login","wrong_password_rejected","fifth_failure_rate_limited","logout","restart_invalidated_session")},"cookie":tunnel["cookie"],"snapshot_api":tunnel["snapshots"],"point_by_point":verify,"openapi_disabled":True,"cors_disabled":True,"point_limit":5000,"response_limit_bytes":2097152}
    ui={"status":"PASS","language":"zh-CN","charts":["cases_new","deaths_new","quality_and_completeness"],"real_snapshots":2,"svg_count":3,"numeric_tables":3,"metadata_cards":8,"desktop":{"viewport":"1440x1000","no_horizontal_overflow":True,"screenshot":"screenshots/dashboard_desktop.png"},"mobile":{"viewport":"390x844","single_column":True,"no_horizontal_overflow":True,"screenshot":"screenshots/dashboard_mobile.png"},"accessibility":{"svg_role_img":True,"aria_labels":True,"keyboard_legends":True,"not_color_only":True},"text_content_only":True,"no_cdn":True,"no_service_worker":True,"no_prediction":True}
    security={"status":"PASS","ssh":pre["ssh"],"database_role":setup["role_tests"],"os_user":setup["os_user"],"scrypt_only_remote":True,"plaintext_dashboard_password_remote":False,"headers":{"csp":True,"frame_ancestors_none":True,"x_frame_options_deny":True,"nosniff":True,"referrer_no_referrer":True,"cache_no_store":True,"permissions_policy":True},"public_8020_blocked":tunnel["public_8020_blocked"],"proxy_variable_hits":final["proxy"]["proxy_variable_hits"],"secret_scan_hits":final["secret_scan_hits"],"openclaw_audits":final["openclaw_audits"],"npm_audit":final["npm_audit"]}
    role={"status":"PASS","role":"epidemic_dashboard_ro","settings":{"login":True,"superuser":False,"createdb":False,"createrole":False,"replication":False,"bypassrls":False,"default_transaction_read_only":"on","statement_timeout":"5s"},"allowed_views":{"catalog":2,"series":69,"quality":30},"denied":final["role_tests"]["denied"],"other_database_denied":True}
    services={"status":"PASS","services":final["services"],"listeners":final["listeners"],"worker_tcp_listener":False,"startup_order":["PostgreSQL","vLLM","Query API","Retrieval Worker","OpenClaw","Dashboard"],"dashboard_idempotent":start["idempotent"],"openclaw_tools":final["catalog"]}
    regression={"status":"PASS","pytest":final["pytest"],"ruff":True,"format":True,"strict_mypy":"51 source files","pip_check":True,"npm_build_test_audit":True,"p0_through_p1_5":True,"postgres_restart_persistence":True,"git_diff_check":True,"large_files":0,"classified_warning":"Starlette TestClient dependency deprecation only"}
    access='''# P1-6 Dashboard access guide

1. Run `F:\\作业\\aibola\\private\\open_p1_6_dashboard.ps1` in PowerShell.
2. Enter the SSH password interactively when `ssh` prompts. The script does not store it.
3. Open `http://127.0.0.1:8020`.
4. Dashboard username: `analyst`.
5. Read the Dashboard password from `F:\\作业\\aibola\\secrets\\dashboard_login.txt`. Do not copy it into scripts or reports.
6. Keep the PowerShell tunnel running while using the page. Closing it makes the server page unavailable locally.

The page only filters already persisted snapshots. It cannot start retrievals, write data, or export raw/CSV files.
'''
    report=f'''# P1-6 Epidemic Dashboard execution report

Final status: **COMPLETED**

## Outcome

Two authorized WHO operational examples were materialized through the existing formal pipeline and retained. A Chinese, authenticated, read-only three-chart Dashboard now reads only three versioned PostgreSQL views through a dedicated least-privilege role and listens only on loopback.

## Evidence

- Git commit: `{final['commit']}`; not pushed; worktree clean.
- Alembic: `0004_p1_6_dashboard_views`; 0003→0004→0003→0004 and single-head checks passed. Only three read-only views were added.
- Formal data: 2 queries, 2 retrieval runs, 2 fetches, 2 raw artifacts, 2 normalization runs, 69 observations, 69 quality assessments, 2 ready snapshots and 69 snapshot members.
- France COVID snapshot: `{ops['datasets'][0]['snapshot_id']}`, 35 rows, SHA-256 `{ops['datasets'][0]['snapshot_sha256']}`; missing fields 17, warnings 0.
- Brazil dengue snapshot: `{ops['datasets'][1]['snapshot_id']}`, 34 rows, SHA-256 `{ops['datasets'][1]['snapshot_sha256']}`; missing fields 0, warnings 0.
- Dashboard API and database views matched point-for-point by canonical SHA-256 for all 69 points and 30 quality periods. Missing data remains absence, not zero; no interpolation, smoothing, resampling, aggregation or prediction occurs.
- Browser QA used real data at desktop and mobile viewports: three SVG charts, three numerical tables, eight metadata cards, responsive single-column mobile layout and no horizontal overflow. Screenshots are included under `screenshots/`.
- Authentication: scrypt hash only on the server, constant-time comparison, 256-bit memory sessions, HttpOnly/SameSite=Strict/Path=/ cookie, 8-hour expiry, fifth-failure lock, POST logout and restart invalidation passed.
- The dedicated database role can SELECT only the three Dashboard views. Base-table reads, writes, TRUNCATE, DDL, `pg_read_file`, role password access, search-path bypass and other-database connection were denied.
- Actual rollback: a temporary restored database was downgraded to 0003 under the P1-5 safe worktree while Dashboard was stopped. The original five services and five OpenClaw tools remained valid and both snapshots were preserved. Reapplication restored 0004, Dashboard and the unchanged formal evidence; temporary resources were removed.
- PostgreSQL restart preserved both snapshot hashes. Six services are healthy. Ports 5432, 8000, 8010, 8020 and 18789 are loopback-only; Worker has no TCP listener.
- Regression: 95 tests passed with one classified dependency deprecation warning. Ruff, format, strict Mypy, pip check, npm build/test/audit, OpenClaw config/doctor/security/deep/gateway, secret scan, proxy scan, diff check and large-file gates passed.

## Access

Run the private local PowerShell tunnel helper, then open `http://127.0.0.1:8020`. The local password file is `F:\\作业\\aibola\\secrets\\dashboard_login.txt`; its contents are not included in any report.

## Semantics and stage boundary

The charts show WHO reported-period data, not infection or event-occurrence dates. WHO does not endorse this system. No papers or new data sources were added, no model was trained, no prediction or outbreak time was calculated, and P1-7 was not entered.
'''
    payloads={"p1_6_database_counts.json":database,"p1_6_migration_results.json":migration_report,"p1_6_operational_datasets.json":ops,"p1_6_dashboard_api_results.json":api,"p1_6_dashboard_ui_results.json":ui,"p1_6_dashboard_security_results.json":security,"p1_6_database_role_results.json":role,"p1_6_service_inventory.json":services,"p1_6_tunnel_results.json":tunnel,"p1_6_regression_results.json":regression,"p1_6_rollback_results.json":rollback}
    s=c.open_sftp()
    for name,value in payloads.items():p0.put(s,REPORT+"/"+name,json.dumps(value,indent=2)+"\n",0o600)
    p0.put(s,REPORT+"/dashboard_access_guide.md",access,0o600);p0.put(s,REPORT+"/P1_6_EPIDEMIC_DASHBOARD_EXECUTION_REPORT.md",report,0o600);s.close()
    pg_bin="USER_HOME/autodl-tmp/conda/envs/postgresql18/bin";pg_run=P+"/storage/database/postgresql18/run";p0.run(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/dropdb')} -h {p0.q(pg_run)} -U epidemicdb --if-exists epidemic_p1_6_test",timeout=300)
    cleanup=P+"/storage/tmp/p1_6_cleanup.py";code='''import pathlib,shutil
root=pathlib.Path("'''+P+'''")
for p in (root/"storage/secrets/p1-6-test.env",):
 if p.exists():p.unlink()
for name in ("p1_6_raw","p1_6_rollback"):
 p=(root/"storage/tmp"/name).resolve();assert p.parent==(root/"storage/tmp").resolve()
 if p.exists():shutil.rmtree(p)''';s=c.open_sftp();p0.put(s,cleanup,code,0o700);s.close();p0.run(c,f"{p0.q(PY)} {p0.q(cleanup)}");assert p0.out(c,f"runuser -u epidemicdb -- {p0.q(pg_bin+'/psql')} -h {p0.q(pg_run)} -U epidemicdb -d postgres -Atc {p0.q("SELECT count(*) FROM pg_database WHERE datname LIKE 'epidemic_p1_6_%'")}")=="0";assert not p0.out(c,f"cd {p0.q(P)} && git status --short").splitlines()
    print(json.dumps({"status":"PASS","reports":13,"temporary_databases_removed":True,"temporary_raw_removed":True,"git_clean":True,"services":final["services"]}))


def pull_reports(c):
    local=Path(r"F:\作业\aibola\reports\p1_6_epidemic_dashboard");local.mkdir(parents=True,exist_ok=True);required=["P1_6_EPIDEMIC_DASHBOARD_EXECUTION_REPORT.md","p1_6_database_counts.json","p1_6_migration_results.json","p1_6_operational_datasets.json","p1_6_dashboard_api_results.json","p1_6_dashboard_ui_results.json","p1_6_dashboard_security_results.json","p1_6_database_role_results.json","p1_6_service_inventory.json","p1_6_tunnel_results.json","p1_6_regression_results.json","p1_6_rollback_results.json","dashboard_access_guide.md"]
    s=c.open_sftp();payloads={}
    for name in required:
        with s.open(REPORT+"/"+name,"rb") as source:payloads[name]=source.read()
    secret_values=[]
    for remote in (P+"/.secrets/postgres.env",P+"/.secrets/p1_1.env",P+"/storage/secrets/query-api-token.env","USER_HOME/autodl-tmp/epidemic-dashboard-runtime/secrets/dashboard.env"):
        try:
            with s.open(remote,"rb") as source:
                for line in source.read().splitlines():
                    if b"=" in line:
                        value=line.split(b"=",1)[1]
                        if len(value)>10:secret_values.append(value)
        except FileNotFoundError:pass
    s.close();host,_,_,password=p0.creds();forbidden=[host.encode(),password.encode(),*secret_values];hits=[]
    for name,data in payloads.items():
        if name.endswith(".json"):json.loads(data)
        if any(x and x in data for x in forbidden) or re.search(rb"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----",data) or re.search(rb"postgresql(?:\+psycopg)?://[^\s]+:[^\s]+@",data) or b"USER_HOME/autodl-tmp/" in data:hits.append(name)
    assert not hits
    for name,data in payloads.items():(local/name).write_bytes(data)
    print(json.dumps({"status":"PASS","local":str(local),"files":required,"json_valid":True,"secret_hits":0,"screenshots_preserved":(local/"screenshots/dashboard_desktop.png").exists() and (local/"screenshots/dashboard_mobile.png").exists()}))


def main():
    parser=argparse.ArgumentParser();parser.add_argument("action",choices=["final-audit-commit","status","reports-cleanup","pull-reports"]);args=parser.parse_args();p13.establish_trust();c=p13.conn()
    try:{"final-audit-commit":final_audit_commit,"status":status,"reports-cleanup":reports_cleanup,"pull-reports":pull_reports}[args.action](c)
    finally:c.close()


if __name__=="__main__":
    try:main()
    except Exception as exc:print("STATUS=BLOCKED");print("REASON="+str(exc));raise SystemExit(1)
