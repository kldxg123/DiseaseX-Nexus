from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import a100_p1_10r as previous


LOCAL_ROOT = Path(__file__).resolve().parents[1]
BASE_ROOT = LOCAL_ROOT / "remote_edit" / "p1_10r3_base"
WORK_ROOT = LOCAL_ROOT / "remote_edit" / "p1_10r3_work"
GATE_ROOT = LOCAL_ROOT / "remote_edit" / "p1_10r3_gate"
REPORT_ROOT = LOCAL_ROOT / "reports" / "p1_10r3_preview_schema_permissions"
INDEX_ROOT = LOCAL_ROOT / "remote_edit" / "p1_10r3_index"
EXPECTED_HEAD = "f9e9c20757c3457f93d4ac2d3076af8760465e39"
REQUEST_ID = "f6f5aeeb3874"
CHANGE_FILES = (
    "migrations/versions/0020_p1_10r3_preview_permissions.py",
    "src/epidemic_core/p1_10/diagnostics.py",
    "src/epidemic_core/p1_10/review_app.py",
    "src/epidemic_core/p1_10/preview.py",
    "src/epidemic_core/discovery/review_app.py",
    "src/epidemic_core/discovery/static/review.p1-10-preview.css",
    "src/epidemic_core/dashboard/app.py",
    "scripts/preview_publication_worker/_common.sh",
    "scripts/preview_publication_worker/start_preview_publication_worker.sh",
    "scripts/preview_publication_worker/status_preview_publication_worker.sh",
    "scripts/source_review/start_source_review.sh",
    "scripts/dashboard/start_dashboard.sh",
    "tests/unit/test_p1_10r3_permissions_and_ux.py",
)
REPORT_FILES = (
    "P1_10R3_PREVIEW_SCHEMA_PERMISSION_REPAIR_REPORT.md",
    "request_f6f5aeeb3874_reconciliation.json",
    "permission_root_cause.json",
    "database_role_inventory.json",
    "permission_matrix_results.json",
    "migration_results.json",
    "default_privilege_results.json",
    "failed_request_recovery.json",
    "candidate_version_results.json",
    "preview_e2e_results.json",
    "dashboard_results.json",
    "trusted_immutability_results.json",
    "service_inventory.json",
    "security_regression_results.json",
    "browser_results.json",
    "git_submission_results.json",
    "USER_RETEST_GUIDE.md",
    "review_console_desktop_1440x900.jpg",
    "review_console_mobile_390x844.jpg",
    "review_candidate_detail.jpg",
    "dashboard_preview_desktop_1440x900.jpg",
    "dashboard_preview_mobile_390x844.jpg",
)
COMMIT_SUBJECT = "fix(p1-10): repair preview schema permissions and candidate UX"


def q(value: str) -> str:
    return shlex.quote(value)


def configure_previous() -> None:
    previous.BASE_ROOT = BASE_ROOT
    previous.WORK_ROOT = WORK_ROOT
    previous.GATE_ROOT = GATE_ROOT
    previous.REPORT_ROOT = REPORT_ROOT
    previous.INDEX_ROOT = INDEX_ROOT
    previous.ISOLATED_STATE = GATE_ROOT / "isolated_stage.json"
    previous.ISOLATED_RUNTIME_STATE = GATE_ROOT / "isolated_runtime.json"
    previous.EXPECTED_HEAD = EXPECTED_HEAD
    previous.CHANGE_FILES = CHANGE_FILES
    previous.OVERLAP_FILES = ()
    previous.REMOTE_FILES = tuple(
        dict.fromkeys(
            (
                *previous.REMOTE_FILES,
                "migrations/versions/0019_p1_10r_formal_preview_http500.py",
                "src/epidemic_core/p1_10/diagnostics.py",
            )
        )
    )


def investigate_action() -> None:
    client, _gate = previous.p19.a100.connect()
    try:
        root, project = previous.p19.a100.paths(client)
        python = f"{root}/conda/envs/epidemic-core/bin/python"
        runtime_code = r'''
import datetime,json,os,pathlib,re
root=pathlib.Path(os.environ["ROOT"]);project=pathlib.Path(os.environ["PROJECT"]);needle=os.environ["REQUEST_ID"]
roots=(root/"epidemic-source-review-runtime"/"log",root/"epidemic-query-access-runtime"/"log",project/"storage"/"logs",root/"postgresql"/"log")
redactions=[
 (re.compile(r"postgresql(?:\+\w+)?://[^\s'\"]+",re.I),"[DATABASE_URL_REDACTED]"),
 (re.compile(r"(?i)\b(authorization|cookie|password|token|secret|api[_-]?key)\b(\s*[=:]\s*)[^\s,;]+"),r"\1\2[REDACTED]"),
 (re.compile(r"(?i)(set-cookie:\s*)[^\r\n]+"),r"\1[REDACTED]"),
]
def clean(line):
 for pattern,replacement in redactions:line=pattern.sub(replacement,line)
 return line
matches=[];inventory=[]
for base in roots:
 if not base.exists():continue
 for path in base.rglob("*.log"):
  try:size=path.stat().st_size
  except OSError:continue
  inventory.append({"path":str(path.relative_to(root)),"bytes":size})
  if size>100_000_000:continue
  try:lines=path.read_text(encoding="utf-8",errors="replace").splitlines()
  except OSError:continue
  indexes=[i for i,line in enumerate(lines) if needle in line]
  for i in indexes:
   lo=max(0,i-45);hi=min(len(lines),i+46)
   matches.append({"path":str(path.relative_to(root)),"line":i+1,"context":[clean(x) for x in lines[lo:hi]]})
print(json.dumps({"request_id":needle,"matches":matches,"log_inventory":inventory,"searched_at_utc":datetime.datetime.now(datetime.timezone.utc).isoformat()},ensure_ascii=False))
'''
        runtime = previous._remote_json(
            client,
            python,
            f"export ROOT={q(root)} PROJECT={q(project)} REQUEST_ID={q(REQUEST_ID)}",
            runtime_code,
            timeout=600,
        )

        setup = (
            f"source {q(project + '/scripts/database/_env.sh')}; set -a; "
            f"source {q(project + '/.secrets/postgres.env')}; set +a"
        )
        database_code = r'''
import json,os
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import make_url
url=make_url(os.environ["MIGRATION_DATABASE_URL"]).set(username="epidemicdb",password=os.environ["POSTGRES_ADMIN_PASSWORD"])
engine=sa.create_engine(url,hide_parameters=True)
roles=("epidemicreview","epidemicpreview","epidemic_dashboard_ro","epidemic_app","epidemic_migrator","epidemicextract","epidemic_daily","epidemic_training_ro","epidemicdb")
preview_tables=("preview_review_decisions","preview_publication_jobs","preview_publication_events","preview_mapping_contracts","preview_staging_observations","preview_observations","preview_quality_assessments","preview_snapshots","preview_snapshot_observations","preview_dashboard_publications")
with engine.connect() as db:
 dbname=db.execute(text("SELECT current_database()" )).scalar_one()
 marker=[dict(r) for r in db.execute(text("SELECT environment,database_name,production,disposable FROM epidemic_preview.environment_markers ORDER BY environment")).mappings()]
 role_rows=[dict(r) for r in db.execute(text("SELECT rolname,rolcanlogin,rolsuper,rolinherit,rolconfig FROM pg_roles WHERE rolname LIKE 'epidemic%' ORDER BY rolname")).mappings()]
 schemas=[dict(r) for r in db.execute(text("SELECT n.nspname schema_name,r.rolname owner FROM pg_namespace n JOIN pg_roles r ON r.oid=n.nspowner WHERE n.nspname IN ('epidemic','epidemic_discovery','epidemic_preview') ORDER BY n.nspname")).mappings()]
 objects=[dict(r) for r in db.execute(text("SELECT n.nspname schema_name,c.relname object_name,c.relkind,r.rolname owner,c.relrowsecurity,c.relforcerowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_roles r ON r.oid=c.relowner WHERE n.nspname IN ('epidemic_discovery','epidemic_preview') AND c.relkind IN ('r','p','v','m','S') ORDER BY n.nspname,c.relkind,c.relname")).mappings()]
 policies=[dict(r) for r in db.execute(text("SELECT schemaname,tablename,policyname,roles,cmd,qual,with_check FROM pg_policies WHERE schemaname IN ('epidemic_discovery','epidemic_preview') ORDER BY schemaname,tablename,policyname")).mappings()]
 default_acls=[dict(r) for r in db.execute(text("SELECT pg_get_userbyid(d.defaclrole) owner,n.nspname schema_name,d.defaclobjtype object_type,d.defaclacl::text acl FROM pg_default_acl d LEFT JOIN pg_namespace n ON n.oid=d.defaclnamespace WHERE n.nspname IN ('epidemic_discovery','epidemic_preview') ORDER BY owner,schema_name,object_type")).mappings()]
 function_acls=[dict(r) for r in db.execute(text("SELECT n.nspname schema_name,p.proname,pg_get_function_identity_arguments(p.oid) arguments,r.rolname owner,p.prosecdef,p.proconfig,p.proacl::text acl FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace JOIN pg_roles r ON r.oid=p.proowner WHERE n.nspname='epidemic_preview' ORDER BY p.proname,arguments")).mappings()]
 privilege=[]
 for role in roles:
  exists=bool(db.execute(text("SELECT 1 FROM pg_roles WHERE rolname=:r"),{"r":role}).scalar_one_or_none())
  if not exists:
   privilege.append({"role":role,"exists":False});continue
  item={"role":role,"exists":True,"schema_usage":{},"schema_create":{},"tables":{},"sequences":{},"functions":{}}
  for schema in ("epidemic","epidemic_discovery","epidemic_preview"):
   item["schema_usage"][schema]=bool(db.execute(text("SELECT has_schema_privilege(:r,:s,'USAGE')"),{"r":role,"s":schema}).scalar_one())
   item["schema_create"][schema]=bool(db.execute(text("SELECT has_schema_privilege(:r,:s,'CREATE')"),{"r":role,"s":schema}).scalar_one())
  for table in preview_tables:
   item["tables"][table]={p:bool(db.execute(text("SELECT has_table_privilege(:r,:o,:p)"),{"r":role,"o":"epidemic_preview."+table,"p":p}).scalar_one()) for p in ("SELECT","INSERT","UPDATE","DELETE","TRUNCATE","REFERENCES","TRIGGER")}
  item["tables"]["candidate_fetches"]={p:bool(db.execute(text("SELECT has_table_privilege(:r,'epidemic_discovery.candidate_fetches',:p)"),{"r":role,"p":p}).scalar_one()) for p in ("SELECT","INSERT","UPDATE","DELETE")}
  item["sequences"]["preview_publication_events_id_seq"]={p:bool(db.execute(text("SELECT has_sequence_privilege(:r,'epidemic_preview.preview_publication_events_id_seq',:p)"),{"r":role,"p":p}).scalar_one()) for p in ("USAGE","SELECT","UPDATE")}
  for sig in ("epidemic_preview.submit_preview_decision(uuid,text,text,uuid,integer,text,jsonb,jsonb,character,jsonb,character,text,boolean)","epidemic_preview.queue_preview_publication(uuid,uuid)","epidemic_preview.revoke_preview_publication(uuid,text,text,uuid)"):
   try:item["functions"][sig]=bool(db.execute(text("SELECT has_function_privilege(:r,:f,'EXECUTE')"),{"r":role,"f":sig}).scalar_one())
   except Exception:item["functions"][sig]=None
  privilege.append(item)
 preview_counts={t:int(db.execute(text("SELECT count(*) FROM epidemic_preview."+t)).scalar_one()) for t in preview_tables}
 request_reconciliation={"preview_counts":preview_counts,"decision_or_job_exists":bool(preview_counts["preview_review_decisions"] or preview_counts["preview_publication_jobs"]),"request_writes":0 if not any(preview_counts.values()) else None}
 result={"database_name":dbname,"marker":marker,"session_user":db.execute(text("SELECT session_user")).scalar_one(),"current_user":db.execute(text("SELECT current_user")).scalar_one(),"roles":role_rows,"schemas":schemas,"objects":objects,"policies":policies,"default_privileges":default_acls,"functions":function_acls,"privilege_matrix":privilege,"request_reconciliation":request_reconciliation}
print(json.dumps(result,default=str))
'''
        database = previous._remote_json(client, python, setup, database_code, timeout=600)
        GATE_ROOT.mkdir(parents=True, exist_ok=True)
        payload = {
            "captured_at_utc": datetime.now(timezone.utc).isoformat(),
            "request": runtime,
            "database": database,
            "secrets_exposed": False,
        }
        (GATE_ROOT / "request_and_privilege_investigation.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    "request_id": REQUEST_ID,
                    "log_matches": len(runtime["matches"]),
                    "matched_logs": sorted({m["path"] for m in runtime["matches"]}),
                    "database_name": database["database_name"],
                    "session_user": database["session_user"],
                    "current_user": database["current_user"],
                    "schemas": database["schemas"],
                    "request_reconciliation": database["request_reconciliation"],
                    "secrets_exposed": False,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        client.close()


def model_action() -> None:
    client, _gate = previous.p19.a100.connect()
    try:
        root, project = previous.p19.a100.paths(client)
        python = f"{root}/conda/envs/epidemic-core/bin/python"
        setup = (
            "set -a; "
            f"source {q(project + '/scripts/database/_env.sh')}; "
            f"source {q(root + '/epidemic-source-review-runtime/secrets/review.env')}; "
            f"source {q(project + '/.secrets/preview.env')}; "
            f"source {q(root + '/epidemic-dashboard-runtime/secrets/dashboard.env')}; "
            f"source {q(project + '/scripts/database/_env.sh')}; "
            f"source {q(project + '/.secrets/postgres.env')}; "
            "set +a"
        )
        code = r'''
import json,os
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import make_url
urls={k:os.environ[k] for k in ("REVIEW_DATABASE_URL","PREVIEW_DATABASE_URL","DASHBOARD_DATABASE_URL","APP_DATABASE_URL","MIGRATION_DATABASE_URL") if os.environ.get(k)}
sessions={}
for name,url in urls.items():
 try:
  engine=sa.create_engine(url,hide_parameters=True,pool_pre_ping=True)
  with engine.connect() as db:
   marker=db.execute(text("SELECT environment,database_name,production,disposable FROM epidemic_preview.environment_markers ORDER BY environment")).mappings().all() if bool(db.execute(text("SELECT to_regclass('epidemic_preview.environment_markers') IS NOT NULL")).scalar_one()) else []
   sessions[name]={"session_user":db.execute(text("SELECT session_user")).scalar_one(),"current_user":db.execute(text("SELECT current_user")).scalar_one(),"database_name":db.execute(text("SELECT current_database()" )).scalar_one(),"marker":[dict(r) for r in marker]}
 except Exception as exc:sessions[name]={"error_type":type(exc).__name__}
admin=sa.create_engine(os.environ["APP_DATABASE_URL"],hide_parameters=True)
with admin.connect() as db:
 tables=("source_candidates","source_candidate_versions","source_review_cases","candidate_fetches","quarantine_artifacts","source_schema_profiles","source_mapping_proposals","source_quality_assessments","discovery_run_candidate_links")
 columns={t:[dict(r) for r in db.execute(text("SELECT column_name,data_type FROM information_schema.columns WHERE table_schema='epidemic_discovery' AND table_name=:t ORDER BY ordinal_position"),{"t":t}).mappings()] for t in tables}
 versions=[dict(r) for r in db.execute(text("""SELECT sc.id candidate_id,sc.publisher,sc.dataset_name,sc.status candidate_status,sc.discovery_run_id source_discovery_run_id,
  sc.canonical_endpoint_sha256,sc.duplicate_source_key,
  cv.id candidate_version_id,cv.version_no,cv.content_sha256,cv.created_at version_created_at,cv.canonical_url_redacted,cv.license_status,
  rc.id review_case_id,rc.state review_state,rc.state_version,sp.row_count,sp.column_count,sp.schema_sha256,sp.date_min,sp.date_max,mp.mapping_sha256,qa.pii_status,qa.license_status assessed_license_status,
  ar.sha256 artifact_sha256,ar.retrieved_at,
  COALESCE(l.discovery_run_id,sc.discovery_run_id) effective_discovery_run_id,l.linked_at,dr.request_json,
  f.attempted_at fetch_attempted_at,f.completed_at fetch_completed_at,f.domain,f.http_status,f.content_type,f.byte_size
 FROM epidemic_discovery.source_candidates sc
 JOIN epidemic_discovery.source_candidate_versions cv ON cv.candidate_id=sc.id
 LEFT JOIN epidemic_discovery.source_review_cases rc ON rc.candidate_version_id=cv.id
 LEFT JOIN epidemic_discovery.source_schema_profiles sp ON sp.candidate_version_id=cv.id
 LEFT JOIN epidemic_discovery.source_mapping_proposals mp ON mp.candidate_version_id=cv.id AND (rc.current_mapping_revision IS NULL OR mp.revision=rc.current_mapping_revision)
 LEFT JOIN epidemic_discovery.source_quality_assessments qa ON qa.candidate_version_id=cv.id
 LEFT JOIN epidemic_discovery.quarantine_artifacts ar ON ar.candidate_version_id=cv.id
 LEFT JOIN LATERAL (SELECT x.discovery_run_id,x.linked_at FROM epidemic_discovery.discovery_run_candidate_links x WHERE x.candidate_version_id=cv.id ORDER BY x.linked_at DESC,x.id DESC LIMIT 1) l ON true
 LEFT JOIN epidemic_discovery.discovery_runs dr ON dr.id=COALESCE(l.discovery_run_id,sc.discovery_run_id)
 LEFT JOIN LATERAL (SELECT x.attempted_at,x.completed_at,x.domain,x.http_status,x.content_type,x.byte_size FROM epidemic_discovery.candidate_fetches x WHERE x.id=ar.fetch_id ORDER BY x.attempted_at DESC LIMIT 1) f ON true
 WHERE lower(sc.dataset_name) LIKE '%sinan%' OR lower(sc.publisher) LIKE '%datasus%'
 ORDER BY sc.dataset_name,COALESCE(ar.retrieved_at,f.completed_at,f.attempted_at,cv.created_at) DESC,cv.version_no DESC""")).mappings()]
 views={name:db.execute(text("SELECT pg_get_viewdef(CAST(:v AS regclass),true)"),{"v":"epidemic_discovery."+name}).scalar_one() for name in ("review_candidate_fetches_v1","review_discovery_search_results_v1","review_preview_v") if db.execute(text("SELECT to_regclass(:v)"),{"v":"epidemic_discovery."+name}).scalar_one_or_none() is not None}
print(json.dumps({"sessions":sessions,"columns":columns,"versions":versions,"views":views},default=str,ensure_ascii=False))
'''
        data = previous._remote_json(client, python, setup, code, timeout=600)
        GATE_ROOT.mkdir(parents=True, exist_ok=True)
        (GATE_ROOT / "candidate_model_and_live_roles.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
        print(json.dumps({"sessions": data["sessions"], "version_count": len(data["versions"]), "versions": data["versions"]}, ensure_ascii=False, indent=2))
    finally:
        client.close()


def stage_lint_fix_action() -> None:
    state = json.loads((GATE_ROOT / "isolated_stage.json").read_text(encoding="utf-8"))
    stage = str(state["stage"])
    client, _gate = previous.p19.a100.connect()
    try:
        root, project = previous.p19.a100.paths(client)
        if not stage.startswith(project + "/storage/p1_10r/stage_"):
            raise SystemExit("P1_10R3_UNSAFE_STAGE")
        python = f"{root}/conda/envs/epidemic-core/bin/python"
        python_files = [item for item in CHANGE_FILES if item.endswith(".py")]
        command = (
            f"cd {q(stage)} && {q(python)} -m ruff check --fix "
            + " ".join(q(item) for item in python_files)
            + f" && {q(python)} -m ruff format "
            + " ".join(q(item) for item in python_files)
        )
        previous.p19.a100.checked(client, f"bash -lc {q(command)}", timeout=600)
        sftp = client.open_sftp()
        try:
            for relative in python_files:
                target = WORK_ROOT / relative
                with sftp.file(f"{stage}/{relative}", "rb") as handle:
                    target.write_bytes(handle.read())
        finally:
            sftp.close()
        print(json.dumps({"ruff_fixed_and_synced": len(python_files), "production_worktree_modified": False}))
    finally:
        client.close()


def e2e_migration_debug_action() -> None:
    state = json.loads((GATE_ROOT / "isolated_stage.json").read_text(encoding="utf-8"))
    stage = str(state["stage"])
    client, _gate = previous.p19.a100.connect()
    try:
        root, project = previous.p19.a100.paths(client)
        python = f"{root}/conda/envs/epidemic-core/bin/python"
        code = r'''
import hashlib,json,os,pathlib,re,secrets,subprocess
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import make_url
stage=pathlib.Path(os.environ["STAGE"]);python=os.environ["PYTHON_BIN"];pgbin=pathlib.Path(os.environ["POSTGRES_BIN"])
source=make_url(os.environ["DEMO_ADMIN_DATABASE_URL"]);cluster=source.set(database="postgres");name="epidemic_p1_10r3_debug_"+secrets.token_hex(5)
dump=stage/"storage"/"isolated-e2e"/"debug.dump";dump.parent.mkdir(parents=True,exist_ok=True)
env=os.environ.copy();env["PGPASSWORD"]=source.password or ""
def pgargs(url):return ["-h",url.host or "127.0.0.1","-p",str(url.port or 5432),"-U",url.username or "p1_9_demo_admin","-d",url.database]
subprocess.run([str(pgbin/"pg_dump"),*pgargs(source),"--format=custom","--file",str(dump)],env=env,check=True,capture_output=True,text=True)
admin=sa.create_engine(cluster,isolation_level="AUTOCOMMIT",hide_parameters=True)
with admin.connect() as db:db.exec_driver_sql('CREATE DATABASE "'+name+'" TEMPLATE template0 ENCODING \'UTF8\'')
try:
 clone=source.set(database=name)
 subprocess.run([str(pgbin/"pg_restore"),*pgargs(clone),"--exit-on-error",str(dump)],env=env,check=True,capture_output=True,text=True)
 migration_env=env.copy();migration_env.update({"MIGRATION_DATABASE_URL":clone.render_as_string(hide_password=False),"PYTHONPATH":str(stage/"src")})
 proc=subprocess.run([python,"-m","alembic","upgrade","head"],cwd=stage,env=migration_env,capture_output=True,text=True,timeout=900)
 safe=re.sub(r"postgresql(?:\+\w+)?://[^\s'\"]+","[DATABASE_URL_REDACTED]",proc.stdout+"\n"+proc.stderr)
 print(json.dumps({"returncode":proc.returncode,"tail":safe.splitlines()[-40:],"output_sha256":hashlib.sha256(safe.encode()).hexdigest()}))
finally:
 with admin.connect() as db:
  db.execute(text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=:name"),{"name":name});db.exec_driver_sql('DROP DATABASE IF EXISTS "'+name+'"')
 admin.dispose()
'''
        setup = (
            f"set -a; source {q(project + '/.secrets/p1_9_demo.env')}; set +a; "
            f"export STAGE={q(stage)} PYTHON_BIN={q(python)} "
            f"POSTGRES_BIN={q(root + '/conda/envs/postgresql18/bin')}"
        )
        result = previous._remote_json(client, python, setup, code, timeout=1800)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        client.close()


def cleanup_stale_e2e_action() -> None:
    state = json.loads((GATE_ROOT / "isolated_stage.json").read_text(encoding="utf-8"))
    stage = str(state["stage"])
    client, _gate = previous.p19.a100.connect()
    try:
        root, project = previous.p19.a100.paths(client)
        python = f"{root}/conda/envs/epidemic-core/bin/python"
        code = r'''
import json,os,pathlib,signal,time
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import make_url
stage=pathlib.Path(os.environ["STAGE"]).resolve();stopped=[]
for proc in pathlib.Path("/proc").iterdir():
 if not proc.name.isdigit():continue
 try:cwd=(proc/"cwd").resolve();cmd=(proc/"cmdline").read_bytes().replace(b"\0",b" ").decode("utf-8","replace")
 except (FileNotFoundError,PermissionError,ProcessLookupError):continue
 if cwd==stage and "uvicorn" in cmd and ("18031" in cmd or "18032" in cmd):
  try:os.killpg(int(proc.name),signal.SIGTERM);stopped.append(int(proc.name))
  except ProcessLookupError:pass
time.sleep(1)
source=make_url(os.environ["DEMO_ADMIN_DATABASE_URL"]);cluster=sa.create_engine(source.set(database="postgres"),isolation_level="AUTOCOMMIT",hide_parameters=True)
with cluster.connect() as db:
 names=[str(x) for x in db.execute(text("SELECT datname FROM pg_database WHERE datname LIKE 'epidemic_p1_10r_e2e_%' OR datname LIKE 'epidemic_p1_10r3_debug_%'")).scalars()]
 for name in names:
  db.execute(text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=:name"),{"name":name});db.exec_driver_sql('DROP DATABASE "'+name+'"')
print(json.dumps({"stopped_services":len(stopped),"dropped_databases":len(names)}))
'''
        setup = (
            f"set -a; source {q(project + '/.secrets/p1_9_demo.env')}; set +a; "
            f"export STAGE={q(stage)}"
        )
        result = previous._remote_json(client, python, setup, code, timeout=300)
        print(json.dumps(result, indent=2))
    finally:
        client.close()


def migration_gate_action() -> None:
    state = json.loads((GATE_ROOT / "isolated_stage.json").read_text(encoding="utf-8"))
    stage = str(state["stage"])
    client, _gate = previous.p19.a100.connect()
    try:
        root, project = previous.p19.a100.paths(client)
        python = f"{root}/conda/envs/epidemic-core/bin/python"
        code = r'''
import datetime as dt,hashlib,json,os,pathlib,re,secrets,subprocess
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import make_url
project=pathlib.Path(os.environ["PROJECT"]);stage=pathlib.Path(os.environ["STAGE"]);python=os.environ["PYTHON_BIN"];pgbin=pathlib.Path(os.environ["POSTGRES_BIN"])
formal=make_url(os.environ["MIGRATION_DATABASE_URL"]).set(username="epidemicdb",password=os.environ["POSTGRES_ADMIN_PASSWORD"]);cluster=formal.set(database="postgres")
stamp=dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ");backup_dir=project/"storage/backups/p1_10r3"/stamp;backup_dir.mkdir(parents=True,exist_ok=False,mode=0o700);dump=backup_dir/"pre_migration.dump"
env=os.environ.copy();env["PGPASSWORD"]=formal.password or "";checks=[]
def pgargs(database):return ["-h",formal.host or "127.0.0.1","-p",str(formal.port or 5432),"-U",formal.username or "epidemicdb","-d",database]
def run(label,args,timeout=1800,extra=None):
 e=env.copy();e.update(extra or {});p=subprocess.run(args,cwd=stage,env=e,text=True,capture_output=True,timeout=timeout)
 safe=re.sub(r"postgresql(?:\+\w+)?://[^\s'\"]+","[DATABASE_URL_REDACTED]",p.stdout+"\n"+p.stderr).replace(str(project),"[project]").replace(str(stage),"[isolated-project]")
 checks.append({"label":label,"returncode":p.returncode,"output_sha256":hashlib.sha256(safe.encode()).hexdigest(),"tail":safe.splitlines()[-15:]})
 if p.returncode:raise RuntimeError(label+" failed output_sha256="+hashlib.sha256(safe.encode()).hexdigest())
run("formal_logical_backup",[str(pgbin/"pg_dump"),*pgargs(formal.database),"--format=custom","--file",str(dump)])
if dump.stat().st_size<1024:raise RuntimeError("P1_10R3_BACKUP_INVALID")
dump_sha=hashlib.sha256(dump.read_bytes()).hexdigest();name="epidemic_p1_10r3_restore_"+secrets.token_hex(5)
cluster_engine=sa.create_engine(cluster,isolation_level="AUTOCOMMIT",hide_parameters=True)
with cluster_engine.connect() as db:db.exec_driver_sql('CREATE DATABASE "'+name+'" TEMPLATE template0 ENCODING \'UTF8\'')
trusted_names=("data_sources","query_requests","retrieval_runs","source_fetches","raw_artifacts","normalized_observations","quality_assessments","dataset_snapshots","dataset_snapshot_observations")
def inventory(engine):
 with engine.connect() as db:return {"counts":{n:int(db.execute(text("SELECT count(*) FROM epidemic."+n)).scalar_one()) for n in trusted_names},"snapshots":[dict(r) for r in db.execute(text("SELECT id,row_count,content_sha256 FROM epidemic.dataset_snapshots ORDER BY created_at,id")).mappings()]}
source_engine=sa.create_engine(formal,hide_parameters=True);source=inventory(source_engine)
try:
 run("restore_current_backup",[str(pgbin/"pg_restore"),*pgargs(name),"--exit-on-error","--no-owner",str(dump)])
 test_url=formal.set(database=name);test_engine=sa.create_engine(test_url,hide_parameters=True);restored=inventory(test_engine)
 if restored!=source:raise RuntimeError("P1_10R3_RESTORED_INVENTORY_MISMATCH")
 migration_env=env.copy();migration_env.update({"MIGRATION_DATABASE_URL":test_url.render_as_string(hide_password=False),"PYTHONPATH":str(stage/"src")})
 run("upgrade_0020",[python,"-m","alembic","upgrade","head"],900,migration_env)
 with test_engine.connect() as db:
  revision=db.execute(text("SELECT version_num FROM epidemic.alembic_version")).scalar_one()
  view_names=("preview_candidate_version_history_v1","preview_candidate_dataset_summary_v1","preview_candidate_fetches_safe_v1","runtime_readiness_v1")
  views={v:bool(db.execute(text("SELECT to_regclass(:v) IS NOT NULL"),{"v":"epidemic_preview."+v}).scalar_one()) for v in view_names}
  owners={r[0]:r[1] for r in db.execute(text("SELECT c.relname,pg_get_userbyid(c.relowner) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='epidemic_preview' AND c.relname=ANY(:names)"),{"names":list(view_names)}).all()}
  summary=[dict(r) for r in db.execute(text("SELECT dataset_name,current_version_no,version_count,current_artifact_sha256,current_retrieved_at,row_count,column_count,preview_eligible FROM epidemic_preview.preview_candidate_dataset_summary_v1 ORDER BY current_retrieved_at DESC")).mappings()]
  history=[dict(r) for r in db.execute(text("SELECT logical_version_no,is_current,superseded,artifact_sha256,retrieved_at,row_count,column_count,content_changed,row_count_delta,schema_changed,date_range_changed,safe_fields_changed,mapping_still_valid,difference_summary FROM epidemic_preview.preview_candidate_version_history_v1 ORDER BY logical_dataset_key,retrieved_at DESC")).mappings()]
  def hp(role,kind,target,priv):
   if kind=="schema":return bool(db.execute(text("SELECT has_schema_privilege(:r,:t,:p)"),{"r":role,"t":target,"p":priv}).scalar_one())
   if kind=="table":return bool(db.execute(text("SELECT has_table_privilege(:r,:t,:p)"),{"r":role,"t":target,"p":priv}).scalar_one())
   if kind=="sequence":return bool(db.execute(text("SELECT has_sequence_privilege(:r,:t,:p)"),{"r":role,"t":target,"p":priv}).scalar_one())
   return bool(db.execute(text("SELECT has_function_privilege(:r,:t,:p)"),{"r":role,"t":target,"p":priv}).scalar_one())
  permissions={
   "review_schema_usage":hp("epidemicreview","schema","epidemic_preview","USAGE"),
   "review_dataset_summary_select":hp("epidemicreview","table","epidemic_preview.preview_candidate_dataset_summary_v1","SELECT"),
   "review_history_select":hp("epidemicreview","table","epidemic_preview.preview_candidate_version_history_v1","SELECT"),
   "review_fetch_safe_select":hp("epidemicreview","table","epidemic_preview.preview_candidate_fetches_safe_v1","SELECT"),
   "review_decision_execute":hp("epidemicreview","function","epidemic_preview.submit_preview_decision(uuid,text,text,uuid,integer,text,jsonb,jsonb,character,jsonb,character,text,boolean)","EXECUTE"),
   "review_queue_execute":hp("epidemicreview","function","epidemic_preview.queue_preview_publication(uuid,uuid)","EXECUTE"),
   "review_base_fetch_denied":not hp("epidemicreview","table","epidemic_discovery.candidate_fetches","SELECT"),
   "review_observation_insert_denied":not hp("epidemicreview","table","epidemic_preview.preview_observations","INSERT"),
   "review_snapshot_insert_denied":not hp("epidemicreview","table","epidemic_preview.preview_snapshots","INSERT"),
   "worker_schema_usage":hp("epidemicpreview","schema","epidemic_preview","USAGE"),
   "worker_job_update":hp("epidemicpreview","table","epidemic_preview.preview_publication_jobs","UPDATE"),
   "worker_staging_insert":hp("epidemicpreview","table","epidemic_preview.preview_staging_observations","INSERT"),
   "worker_staging_delete":hp("epidemicpreview","table","epidemic_preview.preview_staging_observations","DELETE"),
   "worker_observation_insert":hp("epidemicpreview","table","epidemic_preview.preview_observations","INSERT"),
   "worker_snapshot_insert":hp("epidemicpreview","table","epidemic_preview.preview_snapshots","INSERT"),
   "worker_publication_insert":hp("epidemicpreview","table","epidemic_preview.preview_dashboard_publications","INSERT"),
   "worker_event_sequence_usage":hp("epidemicpreview","sequence","epidemic_preview.preview_publication_events_id_seq","USAGE"),
   "worker_trusted_insert_denied":not hp("epidemicpreview","table","epidemic.normalized_observations","INSERT"),
   "worker_daily_update_denied":not hp("epidemicpreview","table","epidemic.daily_update_schedules","UPDATE"),
   "dashboard_active_select":hp("epidemic_dashboard_ro","table","epidemic_preview.active_preview_publications_v1","SELECT"),
   "dashboard_series_select":hp("epidemic_dashboard_ro","table","epidemic_preview.preview_dashboard_series_v1","SELECT"),
   "dashboard_write_denied":not hp("epidemic_dashboard_ro","table","epidemic_preview.preview_observations","INSERT"),
   "dashboard_raw_denied":not hp("epidemic_dashboard_ro","table","epidemic_discovery.quarantine_artifacts","SELECT"),
   "daily_preview_write_denied":not hp("epidemic_app","table","epidemic_preview.preview_observations","INSERT"),
   "public_schema_create_denied":not hp("public","schema","epidemic_preview","CREATE"),
   "public_preview_write_denied":not hp("public","table","epidemic_preview.preview_observations","INSERT"),
   "public_submit_execute_denied":not hp("public","function","epidemic_preview.submit_preview_decision(uuid,text,text,uuid,integer,text,jsonb,jsonb,character,jsonb,character,text,boolean)","EXECUTE"),
  }
  default_acl=[dict(r) for r in db.execute(text("SELECT pg_get_userbyid(d.defaclrole) owner,n.nspname schema_name,d.defaclobjtype object_type,d.defaclacl::text acl FROM pg_default_acl d LEFT JOIN pg_namespace n ON n.oid=d.defaclnamespace WHERE (n.nspname='epidemic_preview' OR (d.defaclobjtype='f' AND pg_get_userbyid(d.defaclrole)='epidemicdb')) ORDER BY owner,object_type")).mappings()]
  rls=[dict(r) for r in db.execute(text("SELECT schemaname,tablename,policyname,roles,cmd FROM pg_policies WHERE schemaname='epidemic_preview' ORDER BY tablename,policyname")).mappings()]
 run("downgrade_0019",[python,"-m","alembic","downgrade","0019_p1_10r_formal_preview_http500"],900,migration_env)
 with test_engine.connect() as db:down_revision=db.execute(text("SELECT version_num FROM epidemic.alembic_version")).scalar_one();views_absent=all(db.execute(text("SELECT to_regclass(:v) IS NULL"),{"v":"epidemic_preview."+v}).scalar_one() for v in view_names[:-1])
 run("reupgrade_0020",[python,"-m","alembic","upgrade","head"],900,migration_env);run("single_head",[python,"-m","alembic","heads"],300,migration_env)
 with test_engine.connect() as db:final_revision=db.execute(text("SELECT version_num FROM epidemic.alembic_version")).scalar_one()
 final_inventory=inventory(test_engine);test_engine.dispose()
 candidate_ok=any(x["current_version_no"]==2 and x["version_count"]==2 for x in summary) and any(bool(x["is_current"]) and x["logical_version_no"]==2 for x in history) and any(bool(x["superseded"]) and x["logical_version_no"]==1 for x in history)
 default_ok=any(x["owner"]=="epidemicdb" and x["object_type"]=="r" and "epidemicpreview" in str(x["acl"]) for x in default_acl) and any(x["owner"]=="epidemicdb" and x["object_type"]=="S" and "epidemicpreview" in str(x["acl"]) for x in default_acl) and any(x["owner"]=="epidemicdb" and x["object_type"]=="f" and not str(x["acl"]).startswith("{=X") and ",=X" not in str(x["acl"]) for x in default_acl)
 passed=revision=="0020_p1_10r3_preview_permissions" and all(views.values()) and all(x=="epidemicdb" for x in owners.values()) and all(permissions.values()) and default_ok and candidate_ok and not rls and down_revision=="0019_p1_10r_formal_preview_http500" and views_absent and final_revision==revision and final_inventory==source
 if not passed:
  failed_permissions=[k for k,v in permissions.items() if not v]
  candidate_diag=[(x.get("current_version_no"),x.get("version_count")) for x in summary]
  default_diag=[(x.get("owner"),x.get("object_type"),x.get("acl")) for x in default_acl]
  raise RuntimeError("P1_10R3_GATE failed="+",".join(failed_permissions)+" default="+str(default_diag)+" candidate="+str(candidate_diag)+" history="+str(len(history)))
 print(json.dumps({"pass":True,"backup":{"sha256":dump_sha,"bytes":dump.stat().st_size,"path":"[project]/storage/backups/p1_10r3/<timestamp>/pre_migration.dump"},"revision":revision,"views":views,"view_owners":owners,"permissions":permissions,"default_privileges":default_acl,"rls":rls,"candidate_versions":{"summary":summary,"history":history},"downgraded_revision":down_revision,"reupgraded_revision":final_revision,"restored_inventory_match":True,"trusted_inventory":source,"checks":checks,"formal_database_modified":False},default=str,ensure_ascii=False))
finally:
 with cluster_engine.connect() as db:
  db.execute(text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=:name"),{"name":name});db.exec_driver_sql('DROP DATABASE IF EXISTS "'+name+'"')
 cluster_engine.dispose();source_engine.dispose()
'''
        setup = (
            f"source {q(project + '/scripts/database/_env.sh')}; set -a; "
            f"source {q(project + '/.secrets/postgres.env')}; set +a; "
            f"export PROJECT={q(project)} STAGE={q(stage)} PYTHON_BIN={q(python)} "
            f"POSTGRES_BIN={q(root + '/conda/envs/postgresql18/bin')}"
        )
        result = previous._remote_json(client, python, setup, code, timeout=5400)
        GATE_ROOT.mkdir(parents=True, exist_ok=True)
        (GATE_ROOT / "migration_and_permission_results.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    finally:
        client.close()


def deploy_action() -> None:
    capture = json.loads((GATE_ROOT / "capture.json").read_text(encoding="utf-8"))
    before = json.loads((GATE_ROOT / "production_scene.json").read_text(encoding="utf-8"))["database"]
    client, _gate = previous.p19.a100.connect()
    try:
        root, project = previous.p19.a100.paths(client)
        if previous.p19.a100.checked(client, f"git -C {q(project)} rev-parse HEAD") != EXPECTED_HEAD:
            raise SystemExit("P1_10R3_DEPLOY_HEAD_CHANGED")
        if previous.p19.a100.checked(client, f"git -C {q(project)} diff --cached --name-only"):
            raise SystemExit("P1_10R3_DEPLOY_INDEX_NOT_EMPTY")
        prior_deployed = {
            "src/epidemic_core/p1_10/preview.py": "8b7e79f80972227f6ea6f258b3d24d977e339cd1f0fe1c6cfce30d5db03d050e",
            "tests/unit/test_p1_10r3_permissions_and_ux.py": "6e8aa435d6fc8facfaf1197965c7548d7f0d16474932a210c238b9782221de74",
        }
        sftp = client.open_sftp()
        temporary: list[tuple[str, str, int]] = []
        try:
            for relative in CHANGE_FILES:
                source = WORK_ROOT / relative
                if not source.is_file():
                    raise SystemExit(f"P1_10R3_DEPLOY_SOURCE_MISSING:{relative}")
                remote = f"{project}/{relative}"
                expected = capture["files"].get(relative)
                try:
                    with sftp.file(remote, "rb") as handle:
                        current = handle.read()
                    exists = True
                except OSError:
                    current = b""
                    exists = False
                desired = source.read_bytes()
                desired_sha = __import__("hashlib").sha256(desired).hexdigest()
                if expected and expected.get("exists"):
                    if not exists or __import__("hashlib").sha256(current).hexdigest() not in {
                        expected["sha256"],
                        desired_sha,
                        prior_deployed.get(relative, ""),
                    }:
                        raise SystemExit(f"P1_10R3_DEPLOY_CONCURRENT_CHANGE:{relative}")
                    mode = int(expected.get("mode", 0o644))
                else:
                    if exists and __import__("hashlib").sha256(current).hexdigest() not in {
                        desired_sha,
                        prior_deployed.get(relative, ""),
                    }:
                        raise SystemExit(f"P1_10R3_DEPLOY_UNEXPECTED_EXISTING:{relative}")
                    mode = 0o755 if relative.endswith(".sh") else 0o644
                if exists and __import__("hashlib").sha256(current).hexdigest() == desired_sha:
                    continue
                remote_dir = remote.rsplit("/", 1)[0]
                previous.p19.a100.checked(client, f"mkdir -p {q(remote_dir)}")
                tmp = remote + ".p1_10r3_" + __import__("secrets").token_hex(4)
                with sftp.file(tmp, "wb") as handle:
                    handle.write(desired)
                sftp.chmod(tmp, mode)
                temporary.append((tmp, remote, mode))
            for tmp, remote, _mode in temporary:
                sftp.posix_rename(tmp, remote)
        finally:
            for tmp, _remote, _mode in temporary:
                try:
                    sftp.remove(tmp)
                except OSError:
                    pass
            sftp.close()
        targets = " ".join(q(x) for x in CHANGE_FILES)
        previous.p19.a100.checked(client, f"git -C {q(project)} diff --check -- {targets}", timeout=300)
        python = f"{root}/conda/envs/epidemic-core/bin/python"
        code = r'''
import datetime as dt,hashlib,json,os,pathlib,subprocess
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import make_url
project=pathlib.Path(os.environ["PROJECT"]);python=os.environ["PYTHON_BIN"];pgbin=pathlib.Path(os.environ["POSTGRES_BIN"])
url=make_url(os.environ["MIGRATION_DATABASE_URL"]).set(username="epidemicdb",password=os.environ["POSTGRES_ADMIN_PASSWORD"]);env=os.environ.copy();env["MIGRATION_DATABASE_URL"]=url.render_as_string(hide_password=False);env["PGPASSWORD"]=url.password or "";env["PYTHONPATH"]=str(project/"src")
engine=sa.create_engine(url,hide_parameters=True)
with engine.connect() as db:
 revision_before=db.execute(text("SELECT version_num FROM epidemic.alembic_version")).scalar_one()
 preview_before={t:int(db.execute(text("SELECT count(*) FROM epidemic_preview."+t)).scalar_one()) for t in ("preview_review_decisions","preview_publication_jobs","preview_publication_events","preview_mapping_contracts","preview_staging_observations","preview_observations","preview_quality_assessments","preview_snapshots","preview_snapshot_observations","preview_dashboard_publications")}
stamp=dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ");backup_dir=project/"storage/backups/p1_10r3"/stamp;backup_dir.mkdir(parents=True,exist_ok=False,mode=0o700);dump=backup_dir/"pre_migration.dump"
pgargs=["-h",url.host or "127.0.0.1","-p",str(url.port or 5432),"-U",url.username or "epidemicdb","-d",url.database]
p=subprocess.run([str(pgbin/"pg_dump"),*pgargs,"--format=custom","--file",str(dump)],env=env,text=True,capture_output=True,timeout=1800)
if p.returncode or dump.stat().st_size<1024:raise RuntimeError("P1_10R3_FORMAL_BACKUP_FAILED")
backup_sha=hashlib.sha256(dump.read_bytes()).hexdigest()
p=subprocess.run([python,"-m","alembic","upgrade","head"],cwd=project,env=env,text=True,capture_output=True,timeout=900)
if p.returncode:raise RuntimeError("P1_10R3_FORMAL_MIGRATION_FAILED:"+hashlib.sha256((p.stdout+p.stderr).encode()).hexdigest())
with engine.connect() as db:
 revision_after=db.execute(text("SELECT version_num FROM epidemic.alembic_version")).scalar_one()
 preview_after={t:int(db.execute(text("SELECT count(*) FROM epidemic_preview."+t)).scalar_one()) for t in preview_before}
print(json.dumps({"revision_before":revision_before,"revision_after":revision_after,"backup":{"sha256":backup_sha,"bytes":dump.stat().st_size,"path":"[project]/storage/backups/p1_10r3/<timestamp>/pre_migration.dump"},"preview_before":preview_before,"preview_after":preview_after,"formal_preview_authorization_created":False}))
'''
        setup = (
            f"source {q(project + '/scripts/database/_env.sh')}; set -a; "
            f"source {q(project + '/.secrets/postgres.env')}; set +a; "
            f"export PROJECT={q(project)} PYTHON_BIN={q(python)} "
            f"POSTGRES_BIN={q(root + '/conda/envs/postgresql18/bin')}"
        )
        migration = previous._remote_json(client, python, setup, code, timeout=3600)
        commands = (
            "scripts/source_review/stop_source_review.sh",
            "scripts/dashboard/stop_dashboard.sh",
            "scripts/preview_publication_worker/stop_preview_publication_worker.sh",
            "scripts/source_review/install_runtime.sh",
            "scripts/dashboard/install_runtime.sh",
            "scripts/preview_publication_worker/start_preview_publication_worker.sh",
            "scripts/source_review/start_source_review.sh",
            "scripts/dashboard/start_dashboard.sh",
            "scripts/preview_publication_worker/status_preview_publication_worker.sh",
            "scripts/source_review/status_source_review.sh",
            "scripts/dashboard/status_dashboard.sh",
        )
        service_results: dict[str, bool] = {}
        for relative in commands:
            rc, _out, _err = previous.p19.a100.run(client, q(f"{project}/{relative}"), timeout=300)
            service_results[relative] = rc == 0
            if rc != 0:
                raise SystemExit(f"P1_10R3_DEPLOY_SERVICE_FAILED:{relative}")
        result = {
            "uploaded_paths": list(CHANGE_FILES),
            "migration": migration,
            "service_results": service_results,
            "trusted_counts_before": before["trusted_counts"],
            "snapshot_hashes_before": before["snapshots"],
            "formal_preview_authorization_created": False,
            "trusted_write_performed": False,
            "preexisting_dirty_preserved": True,
        }
        (GATE_ROOT / "formal_deploy_results.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    finally:
        client.close()


def formal_verify_action() -> None:
    before = json.loads((GATE_ROOT / "production_scene.json").read_text(encoding="utf-8"))["database"]
    client, _gate = previous.p19.a100.connect()
    try:
        root, project = previous.p19.a100.paths(client)
        python = f"{root}/conda/envs/epidemic-core/bin/python"
        setup = (
            f"source {q(project + '/scripts/database/_env.sh')}; set -a; "
            f"source {q(project + '/.secrets/postgres.env')}; "
            "export R3_ADMIN_PASSWORD=\"$POSTGRES_ADMIN_PASSWORD\" R3_APP_URL=\"$APP_DATABASE_URL\" R3_MIGRATION_URL=\"$MIGRATION_DATABASE_URL\"; "
            f"source {q(root + '/epidemic-source-review-runtime/secrets/review.env')}; "
            "export R3_REVIEW_URL=\"$REVIEW_DATABASE_URL\"; "
            f"source {q(project + '/.secrets/preview.env')}; "
            "export R3_PREVIEW_URL=\"$PREVIEW_DATABASE_URL\"; "
            f"source {q(root + '/epidemic-dashboard-runtime/secrets/dashboard.env')}; "
            "export R3_DASHBOARD_URL=\"$DASHBOARD_DATABASE_URL\"; "
            "set +a"
        )
        code = r'''
import json,os,pathlib,urllib.request
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import make_url
urls={"review":os.environ["R3_REVIEW_URL"],"preview_worker":os.environ["R3_PREVIEW_URL"],"dashboard":os.environ["R3_DASHBOARD_URL"],"query_api":os.environ["R3_APP_URL"],"retrieval_discovery":os.environ["R3_APP_URL"],"daily_update":os.environ["R3_APP_URL"],"training_reader":os.environ["R3_APP_URL"],"migration_config":os.environ["R3_MIGRATION_URL"]}
roles={}
for label,url in urls.items():
 engine=sa.create_engine(url,hide_parameters=True,pool_pre_ping=True)
 with engine.connect() as db:roles[label]={"session_user":db.execute(text("SELECT session_user")).scalar_one(),"current_user":db.execute(text("SELECT current_user")).scalar_one(),"database_name":db.execute(text("SELECT current_database()" )).scalar_one()}
 engine.dispose()
owner_url=make_url(os.environ["R3_MIGRATION_URL"]).set(username="epidemicdb",password=os.environ["R3_ADMIN_PASSWORD"]);owner=sa.create_engine(owner_url,hide_parameters=True)
trusted_names=("data_sources","query_requests","retrieval_runs","source_fetches","raw_artifacts","normalized_observations","quality_assessments","dataset_snapshots","dataset_snapshot_observations")
preview_names=("preview_review_decisions","preview_publication_jobs","preview_publication_events","preview_mapping_contracts","preview_staging_observations","preview_observations","preview_quality_assessments","preview_snapshots","preview_snapshot_observations","preview_dashboard_publications")
with owner.connect() as db:
 roles["migration_object_creator"]={"session_user":db.execute(text("SELECT session_user")).scalar_one(),"current_user":db.execute(text("SELECT current_user")).scalar_one(),"database_name":db.execute(text("SELECT current_database()" )).scalar_one()}
 trusted={n:int(db.execute(text("SELECT count(*) FROM epidemic."+n)).scalar_one()) for n in trusted_names};preview={n:int(db.execute(text("SELECT count(*) FROM epidemic_preview."+n)).scalar_one()) for n in preview_names}
 snapshots=[dict(r) for r in db.execute(text("SELECT id,row_count,content_sha256,created_at FROM epidemic.dataset_snapshots ORDER BY created_at,id")).mappings()]
 revision=db.execute(text("SELECT version_num FROM epidemic.alembic_version")).scalar_one();marker=[dict(r) for r in db.execute(text("SELECT environment,database_name,production,disposable FROM epidemic_preview.environment_markers ORDER BY environment")).mappings()]
 summary=[dict(r) for r in db.execute(text("SELECT dataset_name,publisher,country_iso3,disease_code,current_version_no,version_count,current_artifact_sha256,current_retrieved_at,row_count,column_count,license_status,preview_eligible,preview_state FROM epidemic_preview.preview_candidate_dataset_summary_v1 ORDER BY current_retrieved_at DESC")).mappings()]
 history=[dict(r) for r in db.execute(text("SELECT dataset_name,logical_version_no,is_current,superseded,artifact_sha256,schema_sha256,retrieved_at,row_count,column_count,content_changed,row_count_delta,schema_changed,date_range_changed,safe_fields_changed,mapping_still_valid,difference_summary,preview_decision_id,publication_id FROM epidemic_preview.preview_candidate_version_history_v1 ORDER BY logical_dataset_key,retrieved_at DESC")).mappings()]
 schedules=[dict(r) for r in db.execute(text("SELECT schedule_id,schedule_name,enabled,next_due_at,last_success_at,consecutive_failures,state_version FROM epidemic.daily_update_schedules ORDER BY schedule_name")).mappings()]
 default_acl=[dict(r) for r in db.execute(text("SELECT pg_get_userbyid(d.defaclrole) owner,n.nspname schema_name,d.defaclobjtype object_type,d.defaclacl::text acl FROM pg_default_acl d LEFT JOIN pg_namespace n ON n.oid=d.defaclnamespace WHERE (n.nspname='epidemic_preview' OR (d.defaclobjtype='f' AND pg_get_userbyid(d.defaclrole)='epidemicdb')) ORDER BY owner,object_type")).mappings()]
 role_exists={r:bool(db.execute(text("SELECT 1 FROM pg_roles WHERE rolname=:r"),{"r":r}).scalar_one_or_none()) for r in ("epidemicreview","epidemicpreview","epidemic_dashboard_ro","epidemic_app","epidemic_migrator","epidemicextract","epidemic_training_ro","epidemic_daily")}
 request_writes={"decision":int(db.execute(text("SELECT count(*) FROM epidemic_preview.preview_review_decisions")).scalar_one()),"job":int(db.execute(text("SELECT count(*) FROM epidemic_preview.preview_publication_jobs")).scalar_one()),"event":int(db.execute(text("SELECT count(*) FROM epidemic_preview.preview_publication_events")).scalar_one())}
 review_url=os.environ["R3_REVIEW_URL"];review=sa.create_engine(review_url,hide_parameters=True)
with review.connect() as db:
 review_read={"dataset_summary":int(db.execute(text("SELECT count(*) FROM epidemic_preview.preview_candidate_dataset_summary_v1")).scalar_one()),"version_history":int(db.execute(text("SELECT count(*) FROM epidemic_preview.preview_candidate_version_history_v1")).scalar_one()),"safe_fetch":int(db.execute(text("SELECT count(*) FROM epidemic_preview.preview_candidate_fetches_safe_v1")).scalar_one())}
ready={}
for label,port in (("review",8021),("dashboard",8020)):
 try:
  with urllib.request.urlopen(f"http://127.0.0.1:{port}/readyz",timeout=5) as response:ready[label]={"status":response.status,"body":json.loads(response.read())}
 except Exception as exc:ready[label]={"status":None,"error_type":type(exc).__name__}
project=pathlib.Path(os.environ["PROJECT"]);pidfiles={"review":project.parent/"epidemic-source-review-runtime/run/review.pid","dashboard":project.parent/"epidemic-dashboard-runtime/run/dashboard.pid","preview_worker":project/"storage/run/preview-publication-worker/worker.pid","discovery_worker":project/"storage/run/source-discovery-worker/worker.pid"};runtime={}
for label,path in pidfiles.items():
 try:pid=int(path.read_text().strip());env=(pathlib.Path("/proc")/str(pid)/"environ").read_bytes();keys={x.split(b"=",1)[0] for x in env.split(b"\0") if b"=" in x};runtime[label]={"alive":(pathlib.Path("/proc")/str(pid)).exists(),"proxy_variables_present":bool(keys&{b"http_proxy",b"https_proxy",b"HTTP_PROXY",b"HTTPS_PROXY",b"all_proxy",b"ALL_PROXY"})}
 except Exception:runtime[label]={"alive":False,"proxy_variables_present":False}
print(json.dumps({"roles":roles,"role_exists":role_exists,"revision":revision,"marker":marker,"trusted_counts":trusted,"preview_counts":preview,"snapshots":snapshots,"training_rows":int(sa.create_engine(owner_url,hide_parameters=True).connect().execute(text("SELECT count(*) FROM epidemic.training_snapshot_observations_v1")).scalar_one()),"summary":summary,"history":history,"daily_schedules":schedules,"default_privileges":default_acl,"request_reconciliation":request_writes,"review_safe_reads":review_read,"ready":ready,"runtime":runtime},default=str,ensure_ascii=False))
'''
        data = previous._remote_json(
            client,
            python,
            setup + f"; export PROJECT={q(project)}",
            code,
            timeout=900,
        )
        ports = previous.p19.a100.checked(
            client,
            "ss -ltnH '( sport = :8020 or sport = :8021 or sport = :8010 or sport = :18789 or sport = :8000 )'",
        )
        data["loopback_only"] = all("127.0.0.1:" in line or "[::1]:" in line for line in ports.splitlines())
        service_commands = {
            "preview_worker": f"{project}/scripts/preview_publication_worker/status_preview_publication_worker.sh",
            "daily_update": f"{project}/scripts/daily_update/status_daily_update.sh",
            "source_discovery_worker": f"{project}/scripts/source_discovery_worker/status_source_discovery_worker.sh",
            "openclaw_gateway": f"{project}/scripts/openclaw/status_openclaw.sh",
        }
        data["service_status"] = {}
        for label, command in service_commands.items():
            rc, _out, _err = previous.p19.a100.run(client, q(command), timeout=120)
            data["service_status"][label] = rc == 0
        data["trusted_counts_unchanged"] = data["trusted_counts"] == before["trusted_counts"]
        data["snapshot_hashes_unchanged"] = data["snapshots"] == before["snapshots"]
        data["formal_preview_all_zero"] = all(value == 0 for value in data["preview_counts"].values())
        data["pass"] = (
            data["revision"] == "0020_p1_10r3_preview_permissions"
            and data["trusted_counts_unchanged"]
            and data["snapshot_hashes_unchanged"]
            and data["formal_preview_all_zero"]
            and data["loopback_only"]
            and all(data["service_status"].values())
            and all(item.get("status") == 200 and item.get("body", {}).get("ready") for item in data["ready"].values())
            and all(item["alive"] and not item["proxy_variables_present"] for item in data["runtime"].values())
        )
        (GATE_ROOT / "formal_verification_results.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
        print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))
        if not data["pass"]:
            raise SystemExit("P1_10R3_FORMAL_VERIFICATION_FAILED")
    finally:
        client.close()


def launcher_gate_action() -> None:
    client, gate = previous.p19.a100.connect()
    try:
        root, project = previous.p19.a100.paths(client)
        python = f"{root}/conda/envs/epidemic-core/bin/python"
        code = r'''import json,os,pathlib,subprocess,urllib.request
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import make_url
project=os.environ["PROJECT"]
url=make_url(os.environ["MIGRATION_DATABASE_URL"]).set(username="epidemicdb",password=os.environ["POSTGRES_ADMIN_PASSWORD"])
engine=sa.create_engine(url,hide_parameters=True)
with engine.connect() as db:
 revision=db.execute(text("SELECT version_num FROM epidemic.alembic_version")).scalar_one()
 marker=bool(db.execute(text("SELECT production AND NOT disposable AND database_name=current_database() FROM epidemic_preview.environment_markers WHERE environment='production'")).scalar_one())
subject=subprocess.run(["git","-C",project,"show","-s","--format=%s","HEAD"],text=True,capture_output=True,check=True).stdout.strip()
ready={}
for name,port in (("dashboard",8020),("review",8021)):
 try:
  with urllib.request.urlopen(f"http://127.0.0.1:{port}/readyz",timeout=5) as response:
   body=json.loads(response.read());ready[name]=response.status==200 and body.get("ready") is True
 except Exception:ready[name]=False
worker=subprocess.run([project+"/scripts/preview_publication_worker/status_preview_publication_worker.sh"],capture_output=True,text=True)
proxy=any(os.environ.get(name) for name in ("http_proxy","https_proxy","HTTP_PROXY","HTTPS_PROXY","all_proxy","ALL_PROXY"))
print(json.dumps({"revision":revision,"production_marker":marker,"commit_subject_match":subject==os.environ["SUBJECT"],"services_ready":ready,"preview_worker_ready":worker.returncode==0,"proxy_variables_present":proxy}))
'''
        setup = (
            f"source {q(project + '/scripts/database/_env.sh')}; set -a; "
            f"source {q(project + '/.secrets/postgres.env')}; set +a; "
            f"export PROJECT={q(project)} SUBJECT={q(COMMIT_SUBJECT)}; "
            "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY"
        )
        result = previous._remote_json(client, python, setup, code, timeout=300)
        result["target_identity_verified"] = bool(gate.get("stable_second_connection"))
        passed = (
            result["revision"] == "0020_p1_10r3_preview_permissions"
            and result["production_marker"]
            and result["commit_subject_match"]
            and all(result["services_ready"].values())
            and result["preview_worker_ready"]
            and not result["proxy_variables_present"]
            and result["target_identity_verified"]
        )
        print(json.dumps({"pass": passed, "gate": result}, ensure_ascii=True, indent=2, sort_keys=True))
        if not passed:
            raise SystemExit("P1_10R3_LAUNCHER_GATE_FAILED")
    finally:
        client.close()


def upload_reports_action() -> None:
    missing = [name for name in REPORT_FILES if not (REPORT_ROOT / name).is_file()]
    if missing:
        raise SystemExit("P1_10R3_REPORTS_MISSING:" + ",".join(missing))
    client, _gate = previous.p19.a100.connect()
    try:
        _root, project = previous.p19.a100.paths(client)
        remote_root = f"{project}/reports/p1_10r3_preview_schema_permissions"
        previous.p19.a100.checked(client, f"mkdir -p {q(remote_root)} && chmod 750 {q(remote_root)}")
        sftp = client.open_sftp()
        try:
            for name in REPORT_FILES:
                source = REPORT_ROOT / name
                remote = f"{remote_root}/{name}"
                tmp = f"{remote}.tmp_{secrets.token_hex(4)}"
                sftp.put(str(source), tmp)
                sftp.chmod(tmp, 0o640)
                sftp.posix_rename(tmp, remote)
        finally:
            sftp.close()
        print(json.dumps({"uploaded": len(REPORT_FILES), "credential_values_exposed": False}))
    finally:
        client.close()


def commit_action() -> None:
    capture = json.loads((GATE_ROOT / "capture.json").read_text(encoding="utf-8"))
    client, _gate = previous.p19.a100.connect()
    try:
        root, project = previous.p19.a100.paths(client)
        if previous.p19.a100.checked(client, f"git -C {q(project)} rev-parse HEAD") != EXPECTED_HEAD:
            raise SystemExit("P1_10R3_COMMIT_HEAD_CHANGED")
        report_paths = [f"reports/p1_10r3_preview_schema_permissions/{name}" for name in REPORT_FILES]
        expected = sorted((*CHANGE_FILES, *report_paths))
        staged_before = previous.p19.a100.checked(client, f"git -C {q(project)} diff --cached --name-only").splitlines()
        if not set(staged_before).issubset(expected):
            raise SystemExit("P1_10R3_COMMIT_INDEX_HAS_UNRELATED_PATHS")
        previous.p19.a100.checked(client, f"git -C {q(project)} add -- " + " ".join(q(x) for x in CHANGE_FILES))
        previous.p19.a100.checked(client, f"git -C {q(project)} add -f -- " + " ".join(q(x) for x in report_paths))
        staged = previous.p19.a100.checked(client, f"git -C {q(project)} diff --cached --name-only").splitlines()
        if sorted(staged) != expected:
            raise SystemExit("P1_10R3_COMMIT_STAGE_SCOPE_MISMATCH")
        previous.p19.a100.checked(client, f"git -C {q(project)} diff --cached --check", timeout=300)
        python = f"{root}/conda/envs/epidemic-core/bin/python"
        scan_code = r'''import hashlib,json,os,re,subprocess
project=os.environ["PROJECT"];paths=json.loads(os.environ["PATHS"])
rules={"private_key":r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----","cloud_token":r"(?:AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9_]{30,}|sk-[A-Za-z0-9]{24,})","credential_url":r"(?:postgresql|https?)://[^\s:/]+:[^\s/@]+@","tls_bypass":r"curl\s+-k(?:\s|$)|wget\s+--no-check-certificate|GIT_SSL_NO_VERIFY\s*=\s*(?:1|true)|pip\s+[^\n]*--trusted-host","proxy_literal":r"(?i)(?:http|https|all)_proxy\s*=\s*['\"]?(?:https?|socks)://"}
findings=[];large=[]
for path in paths:
 data=subprocess.run(["git","-C",project,"show",":"+path],capture_output=True,check=True).stdout
 if len(data)>2*1024*1024:large.append({"path":path,"bytes":len(data)})
 if b"\0" in data:continue
 value=data.decode("utf-8","replace")
 for name,pattern in rules.items():
  for match in re.finditer(pattern,value,re.I|re.M):findings.append({"path":path,"line":value.count("\n",0,match.start())+1,"rule":name,"match_sha256":hashlib.sha256(match.group().encode()).hexdigest()})
print(json.dumps({"findings":findings,"large_files":large}))
'''
        scan = previous._remote_json(
            client,
            python,
            f"export PROJECT={q(project)} PATHS={q(json.dumps(staged))}",
            scan_code,
            timeout=600,
        )
        if scan["findings"] or scan["large_files"]:
            raise SystemExit("P1_10R3_COMMIT_SECURITY_SCAN_FAILED")
        previous.p19.a100.checked(client, f"git -C {q(project)} commit -m {q(COMMIT_SUBJECT)}", timeout=600)
        commit_hash = previous.p19.a100.checked(client, f"git -C {q(project)} rev-parse HEAD")
        subject = previous.p19.a100.checked(client, f"git -C {q(project)} show -s --format=%s HEAD")
        staged_after = previous.p19.a100.checked(client, f"git -C {q(project)} diff --cached --name-only")
        status_after = previous.p19.a100.checked(client, f"git -C {q(project)} status --porcelain=v2 -uall")
        dirty_after = {previous._status_path(line) for line in status_after.splitlines() if line}
        result = {
            "commit": commit_hash,
            "subject": subject,
            "path_count": len(staged),
            "staged_after": bool(staged_after),
            "preexisting_dirty_preserved": set(capture["dirty_paths"]).issubset(dirty_after),
            "preexisting_dirty_count": len(capture["dirty_paths"]),
            "secret_findings": 0,
            "large_files": 0,
            "push": False,
        }
        (GATE_ROOT / "git_commit_results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
        if subject != COMMIT_SUBJECT or staged_after or not result["preexisting_dirty_preserved"]:
            raise SystemExit("P1_10R3_COMMIT_POSTCONDITION_FAILED")
    finally:
        client.close()


def finalize_runtime_action() -> None:
    commit = json.loads((GATE_ROOT / "git_commit_results.json").read_text(encoding="utf-8"))["commit"]
    client, _gate = previous.p19.a100.connect()
    try:
        _root, project = previous.p19.a100.paths(client)
        if previous.p19.a100.checked(client, f"git -C {q(project)} rev-parse HEAD") != commit:
            raise SystemExit("P1_10R3_FINALIZE_COMMIT_MISMATCH")
        commands = (
            "scripts/source_review/stop_source_review.sh",
            "scripts/dashboard/stop_dashboard.sh",
            "scripts/preview_publication_worker/stop_preview_publication_worker.sh",
            "scripts/source_review/start_source_review.sh",
            "scripts/dashboard/start_dashboard.sh",
            "scripts/preview_publication_worker/start_preview_publication_worker.sh",
        )
        for relative in commands:
            rc, _out, _err = previous.p19.a100.run(client, q(f"{project}/{relative}"), timeout=300)
            if rc != 0:
                raise SystemExit(f"P1_10R3_FINALIZE_SERVICE_FAILED:{relative}")
        print(json.dumps({"commit": commit, "services_restarted": True, "formal_preview_authorization_created": False}))
    finally:
        client.close()


def stop_project_action() -> None:
    client, gate = previous.p19.a100.connect()
    try:
        _root, project = previous.p19.a100.paths(client)
        services = (
            ("preview_worker", "scripts/preview_publication_worker/stop_preview_publication_worker.sh", "scripts/preview_publication_worker/status_preview_publication_worker.sh"),
            ("daily_update", "scripts/daily_update/stop_daily_update.sh", "scripts/daily_update/status_daily_update.sh"),
            ("review_console", "scripts/source_review/stop_source_review.sh", "scripts/source_review/status_source_review.sh"),
            ("promotion_worker", "scripts/source_promotion_worker/stop_source_promotion_worker.sh", "scripts/source_promotion_worker/status_source_promotion_worker.sh"),
            ("discovery_worker", "scripts/source_discovery_worker/stop_source_discovery_worker.sh", "scripts/source_discovery_worker/status_source_discovery_worker.sh"),
            ("dashboard", "scripts/dashboard/stop_dashboard.sh", "scripts/dashboard/status_dashboard.sh"),
            ("openclaw", "scripts/openclaw/stop_openclaw.sh", "scripts/openclaw/status_openclaw.sh"),
            ("retrieval_worker", "scripts/retrieval_worker/stop_retrieval_worker.sh", "scripts/retrieval_worker/status_retrieval_worker.sh"),
            ("query_api", "scripts/query_api/stop_query_api.sh", "scripts/query_api/status_query_api.sh"),
            ("vllm", "scripts/qwen_vllm/stop.sh", "scripts/qwen_vllm/status.sh"),
        )
        result: dict[str, dict[str, bool]] = {}
        for name, stop_rel, status_rel in services:
            stop_path = f"{project}/{stop_rel}"
            status_path = f"{project}/{status_rel}"
            exists = previous.p19.a100.run(client, f"test -x {q(stop_path)}", timeout=30)[0] == 0
            if exists:
                previous.p19.a100.run(
                    client,
                    "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; " + q(stop_path),
                    timeout=300,
                )
            stopped = previous.p19.a100.run(client, q(status_path), timeout=120)[0] != 0
            result[name] = {"stop_script_present": exists, "stopped": stopped}
        postgres_running = previous.p19.a100.run(client, q(f"{project}/scripts/postgres/status.sh"), timeout=120)[0] == 0
        passed = all(item["stopped"] for item in result.values()) and postgres_running
        print(
            json.dumps(
                {
                    "pass": passed,
                    "target_identity_verified": bool(gate.get("stable_second_connection")),
                    "services": result,
                    "postgres_preserved": postgres_running,
                    "data_modified": False,
                },
                ensure_ascii=True,
                indent=2,
                sort_keys=True,
            )
        )
        if not passed:
            raise SystemExit("P1_10R3_STOP_PROJECT_FAILED")
    finally:
        client.close()


def commit_code_only_action() -> None:
    client, _gate = previous.p19.a100.connect()
    try:
        _root, project = previous.p19.a100.paths(client)
        if previous.p19.a100.checked(client, f"git -C {q(project)} rev-parse HEAD") != EXPECTED_HEAD:
            raise SystemExit("P1_10R3_COMMIT_HEAD_CHANGED")
        staged_before = previous.p19.a100.checked(client, f"git -C {q(project)} diff --cached --name-only")
        if staged_before:
            raise SystemExit("P1_10R3_COMMIT_INDEX_NOT_EMPTY")
        previous.p19.a100.checked(client, f"git -C {q(project)} add -- " + " ".join(q(x) for x in CHANGE_FILES))
        staged = previous.p19.a100.checked(client, f"git -C {q(project)} diff --cached --name-only").splitlines()
        if sorted(staged) != sorted(CHANGE_FILES):
            raise SystemExit("P1_10R3_COMMIT_STAGE_SCOPE_MISMATCH")
        previous.p19.a100.checked(client, f"git -C {q(project)} diff --cached --check", timeout=300)
        previous.p19.a100.checked(client, f"git -C {q(project)} commit -m {q(COMMIT_SUBJECT)}", timeout=600)
        commit_hash = previous.p19.a100.checked(client, f"git -C {q(project)} rev-parse HEAD")
        subject = previous.p19.a100.checked(client, f"git -C {q(project)} show -s --format=%s HEAD")
        staged_after = previous.p19.a100.checked(client, f"git -C {q(project)} diff --cached --name-only")
        status_after = previous.p19.a100.checked(client, f"git -C {q(project)} status --porcelain=v2 -uall")
        result = {
            "commit": commit_hash,
            "subject": subject,
            "path_count": len(staged),
            "staged_after": bool(staged_after),
            "remaining_dirty_count": len([line for line in status_after.splitlines() if line]),
            "push": False,
        }
        (GATE_ROOT / "git_commit_results.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
        print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
        if subject != COMMIT_SUBJECT or staged_after:
            raise SystemExit("P1_10R3_COMMIT_POSTCONDITION_FAILED")
    finally:
        client.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action",
        choices=(
            "capture",
            "scene",
            "investigate",
            "model",
            "stage",
            "stage-format",
            "stage-lint-fix",
            "stage-tests",
            "full-regression",
            "isolated-e2e",
            "e2e-migration-debug",
            "cleanup-isolated",
            "cleanup-stale-e2e",
            "migration-gate",
            "deploy",
            "formal-verify",
            "launcher-gate",
            "upload-reports",
            "commit",
            "finalize-runtime",
            "stop-project",
            "commit-code-only",
        ),
    )
    args = parser.parse_args()
    configure_previous()
    if args.action == "capture":
        previous.capture_action()
    elif args.action == "scene":
        previous.scene_action()
    elif args.action == "investigate":
        investigate_action()
    elif args.action == "model":
        model_action()
    elif args.action == "stage":
        previous.stage_action()
    elif args.action == "stage-format":
        previous.stage_format_action()
    elif args.action == "stage-lint-fix":
        stage_lint_fix_action()
    elif args.action == "full-regression":
        previous.full_regression_action()
    elif args.action == "isolated-e2e":
        previous.isolated_e2e_action()
    elif args.action == "e2e-migration-debug":
        e2e_migration_debug_action()
    elif args.action == "cleanup-isolated":
        previous.cleanup_isolated_action()
    elif args.action == "cleanup-stale-e2e":
        cleanup_stale_e2e_action()
    elif args.action == "migration-gate":
        migration_gate_action()
    elif args.action == "deploy":
        deploy_action()
    elif args.action == "formal-verify":
        formal_verify_action()
    elif args.action == "launcher-gate":
        launcher_gate_action()
    elif args.action == "upload-reports":
        upload_reports_action()
    elif args.action == "commit":
        commit_action()
    elif args.action == "finalize-runtime":
        finalize_runtime_action()
    elif args.action == "stop-project":
        stop_project_action()
    elif args.action == "commit-code-only":
        commit_code_only_action()
    else:
        previous.stage_tests_action()


if __name__ == "__main__":
    main()
