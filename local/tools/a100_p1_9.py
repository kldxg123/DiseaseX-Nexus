from __future__ import annotations

import argparse
import hashlib
import json
import re
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))

import a100_p1_8ab as a100


LOCAL_ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_ROOT = LOCAL_ROOT / "remote_edit" / "p1_9_base"
SNAPSHOT_FILES = (
    "alembic.ini",
    "pyproject.toml",
    "migrations/env.py",
    "migrations/versions/0001_p1_1_p1_1_domain_storage.py",
    "migrations/versions/0002_p1_3_retrieval_control.py",
    "migrations/versions/0003_p1_4_normalization_pipeline.py",
    "migrations/versions/0004_p1_6_latest_window_dashboard.py",
    "migrations/versions/0005_p1_7_source_discovery_review.py",
    "migrations/versions/0006_p1_7a_ui_read_models.py",
    "migrations/versions/0007_p1_7a_discovery_observability.py",
    "migrations/versions/0008_p1_7a_discovery_capability.py",
    "migrations/versions/0009_p1_7a_keyfree_discovery.py",
    "migrations/versions/0010_p1_8_daily_updates.py",
    "migrations/versions/0011_p1_8b_verified_discovery.py",
    "migrations/versions/0012_p1_8b_discovery_submit_idempotency.py",
    "migrations/versions/0013_p1_8b_discovery_submit_receipt.py",
    "migrations/versions/0014_p1_8b_review_ready_cards.py",
    "migrations/versions/0015_p1_8b_reviewer_read_views.py",
    "src/epidemic_core/db/models/tables.py",
    "src/epidemic_core/discovery/candidate_pipeline.py",
    "src/epidemic_core/discovery/extractor.py",
    "src/epidemic_core/discovery/promotion.py",
    "src/epidemic_core/discovery/providers.py",
    "src/epidemic_core/discovery/review_app.py",
    "src/epidemic_core/discovery/review_labels.py",
    "src/epidemic_core/discovery/review_main.py",
    "src/epidemic_core/discovery/schemas.py",
    "src/epidemic_core/discovery/service.py",
    "src/epidemic_core/discovery/worker.py",
    "src/epidemic_core/discovery/static/review.p1-7ar3-v1.css",
    "src/epidemic_core/discovery/static/review.p1-7ar3-v1.js",
    "src/epidemic_core/dashboard/app.py",
    "src/epidemic_core/dashboard/main.py",
    "src/epidemic_core/dashboard/static/dashboard.p1-7ar-v1.css",
    "src/epidemic_core/dashboard/static/dashboard.p1-7ar-v1.js",
    "src/epidemic_core/dashboard/static/dashboard.p1-8-daily.css",
    "src/epidemic_core/dashboard/static/dashboard.p1-8-daily.js",
    "src/epidemic_core/normalization/service.py",
    "src/epidemic_core/normalization/schemas.py",
    "scripts/source_review/_common.sh",
    "scripts/source_review/configure_database_access.py",
    "scripts/source_review/start_source_review.sh",
    "scripts/source_review/status_source_review.sh",
    "scripts/source_review/stop_source_review.sh",
    "scripts/source_promotion_worker/_common.sh",
    "scripts/source_promotion_worker/start_source_promotion_worker.sh",
    "scripts/source_review/install_runtime.sh",
    "scripts/source_promotion_worker/status_source_promotion_worker.sh",
    "scripts/source_promotion_worker/stop_source_promotion_worker.sh",
    "scripts/dashboard/_common.sh",
    "scripts/dashboard/start_dashboard.sh",
    "scripts/dashboard/status_dashboard.sh",
    "scripts/dashboard/stop_dashboard.sh",
    "scripts/postgres/_common.sh",
    "scripts/postgres/start.sh",
    "scripts/postgres/status.sh",
    "scripts/postgres/stop.sh",
    "tests/unit/test_dashboard.py",
    "tests/unit/test_p1_7a_ui_repair.py",
    "tests/integration/test_discovery_queue_contract.py",
)

P1_9_FILES = (
    "migrations/versions/0016_p1_9_reviewed_promotion.py",
    "migrations/versions/0017_p1_9_crypto_resolution.py",
    "src/epidemic_core/p1_9/__init__.py",
    "src/epidemic_core/p1_9/promotion.py",
    "src/epidemic_core/p1_9/review_app.py",
    "src/epidemic_core/p1_9/dashboard_app.py",
    "src/epidemic_core/p1_9/discovery_worker.py",
    "src/epidemic_core/discovery/promotion.py",
    "src/epidemic_core/discovery/review_app.py",
    "scripts/source_promotion_worker/_common.sh",
    "scripts/source_promotion_worker/start_source_promotion_worker.sh",
    "scripts/source_review/install_runtime.sh",
    "scripts/p1_9_demo/_common.sh",
    "scripts/p1_9_demo/run_discovery_worker.sh",
    "scripts/p1_9_demo/run_promotion_worker.sh",
    "scripts/p1_9_demo/run_review.sh",
    "scripts/p1_9_demo/run_dashboard.sh",
    "scripts/p1_9_demo/start_demo.sh",
    "scripts/p1_9_demo/stop_demo.sh",
    "scripts/p1_9_demo/status_demo.sh",
    "tests/unit/test_p1_9_promotion.py",
    "tests/unit/test_p1_9_ui.py",
)


def q(value: str) -> str:
    return shlex.quote(value)


def remote_json(
    client: Any,
    python: str,
    setup: str,
    code: str,
    *,
    timeout: int = 600,
) -> Any:
    command = f"{setup}; exec {q(python)} -B -c {q(code)}"
    return json.loads(a100.checked(client, f"bash -c {q(command)}", timeout=timeout))


def baseline_action() -> None:
    client, _gate = a100.connect()
    try:
        root, project = a100.paths(client)
        python = root + "/conda/envs/epidemic-core/bin/python"
        formal_code = r'''import hashlib,json,os,pathlib,re,subprocess
import sqlalchemy as sa
from sqlalchemy import text
project=pathlib.Path(os.environ["PROJECT"])
def run(*args):
 return subprocess.run(args,cwd=project,text=True,capture_output=True,check=True).stdout
head=run("git","rev-parse","HEAD").strip()
branch=run("git","branch","--show-current").strip()
porcelain=run("git","status","--porcelain=v2","-uall").splitlines()
staged=run("git","diff","--cached","--binary")
unstaged=run("git","diff","--binary")
untracked=[];sensitive=0
for raw in run("git","ls-files","--others","--exclude-standard").splitlines():
 path=project/raw
 if re.search(r"(?i)(?:^|/)(?:\.env|\.secrets?|private|credentials?|.*(?:token|password|passwd|private[_-]?key).*)(?:/|$)",raw):
  sensitive+=1;continue
 if path.is_file():
  data=path.read_bytes()
  untracked.append({"path":raw,"bytes":len(data),"sha256":hashlib.sha256(data).hexdigest()})
engine=sa.create_engine(os.environ["MIGRATION_DATABASE_URL"],hide_parameters=True)
with engine.connect() as db:
 migration=db.execute(text("SELECT version_num FROM epidemic.alembic_version")).scalar_one()
 names=("data_sources","query_requests","retrieval_runs","source_fetches","raw_artifacts","normalized_observations","quality_assessments","dataset_snapshots","dataset_snapshot_observations")
 counts={name:db.execute(text(f"SELECT count(*) FROM epidemic.{name}")).scalar_one() for name in names}
 snapshots=[dict(row) for row in db.execute(text("SELECT id,content_sha256,row_count,created_at FROM epidemic.dataset_snapshots ORDER BY created_at,id")).mappings()]
print(json.dumps({"git":{"head":head,"branch":branch,"porcelain_v2_count":len(porcelain),"porcelain_v2_sha256":hashlib.sha256("\n".join(porcelain).encode()).hexdigest(),"staged_diff_bytes":len(staged.encode()),"staged_diff_sha256":hashlib.sha256(staged.encode()).hexdigest(),"unstaged_diff_bytes":len(unstaged.encode()),"unstaged_diff_sha256":hashlib.sha256(unstaged.encode()).hexdigest(),"untracked":untracked,"redacted_sensitive_untracked_count":sensitive},"database":{"migration":migration,"counts":counts,"snapshots":snapshots}},default=str))
'''
        result = remote_json(
            client,
            python,
            f"source {q(project + '/scripts/database/_env.sh')}; export PROJECT={q(project)}",
            formal_code,
        )
        discovery_code = r'''import json,os
import sqlalchemy as sa
from sqlalchemy import text
engine=sa.create_engine(os.environ["APP_DATABASE_URL"],hide_parameters=True)
with engine.connect() as db:
 discovery=dict(db.execute(text("SELECT id,state,terminal_reason_code,created_at,completed_at,progress_json FROM epidemic_discovery.discovery_runs WHERE id=CAST(:id AS uuid)"),{"id":"daa30d9d-59bd-4296-8b6d-50bab63fe37f"}).mappings().one())
 candidate=dict(db.execute(text("SELECT c.id candidate_id,c.status candidate_status,cv.id candidate_version_id,cv.content_sha256,rc.id review_case_id,rc.state review_state,qa.sha256 artifact_sha256,qa.byte_size artifact_bytes,sp.row_count,sp.column_count,sp.date_min,sp.date_max,cv.license_status FROM epidemic_discovery.source_candidates c JOIN epidemic_discovery.source_candidate_versions cv ON cv.candidate_id=c.id JOIN epidemic_discovery.source_review_cases rc ON rc.candidate_version_id=cv.id JOIN epidemic_discovery.quarantine_artifacts qa ON qa.candidate_version_id=cv.id JOIN epidemic_discovery.source_schema_profiles sp ON sp.candidate_version_id=cv.id WHERE c.id=CAST(:id AS uuid)"),{"id":"5faf41ca-def4-49f2-bc4a-4cccbb81b909"}).mappings().one())
 decisions=dict(db.execute(text("SELECT count(*) FILTER(WHERE d.decision='APPROVED') approvals,count(*) FILTER(WHERE d.decision='REJECTED') rejections,count(*) total FROM epidemic_discovery.source_review_decisions d JOIN epidemic_discovery.source_review_cases rc ON rc.id=d.review_case_id JOIN epidemic_discovery.source_candidate_versions cv ON cv.id=rc.candidate_version_id WHERE cv.candidate_id=CAST(:id AS uuid)"),{"id":"5faf41ca-def4-49f2-bc4a-4cccbb81b909"}).mappings().one())
 promotions=db.execute(text("SELECT count(*) FROM epidemic_discovery.source_promotion_runs p JOIN epidemic_discovery.source_review_decisions d ON d.id=p.review_decision_id JOIN epidemic_discovery.source_review_cases rc ON rc.id=d.review_case_id JOIN epidemic_discovery.source_candidate_versions cv ON cv.id=rc.candidate_version_id WHERE cv.candidate_id=CAST(:id AS uuid)"),{"id":"5faf41ca-def4-49f2-bc4a-4cccbb81b909"}).scalar_one()
print(json.dumps({"discovery":discovery,"candidate":candidate,"candidate_decisions":decisions,"candidate_promotions":promotions},default=str))
'''
        result.update(
            remote_json(
                client,
                python,
                f"source {q(project + '/scripts/source_discovery_worker/_common.sh')}",
                discovery_code,
            )
        )
        service_paths = {
            "postgres": "scripts/postgres/status.sh",
            "vllm": "scripts/qwen_vllm/status.sh",
            "query_api": "scripts/query_api/status_query_api.sh",
            "retrieval_worker": "scripts/retrieval_worker/status_retrieval_worker.sh",
            "openclaw": "scripts/openclaw/status_openclaw.sh",
            "dashboard": "scripts/dashboard/status_dashboard.sh",
            "discovery_worker": "scripts/source_discovery_worker/status_source_discovery_worker.sh",
            "promotion_worker": "scripts/source_promotion_worker/status_source_promotion_worker.sh",
            "review_console": "scripts/source_review/status_source_review.sh",
            "daily_update": "scripts/daily_update/status_daily_update.sh",
        }
        result["services"] = {
            name: a100.run(client, q(project + "/" + relative), timeout=180)[0] == 0
            for name, relative in service_paths.items()
        }
        port_code = r'''import json,pathlib
wanted={5432,8000,8010,18789,8020,8021};rows=[]
for filename,v6 in (("/proc/net/tcp",False),("/proc/net/tcp6",True)):
 for line in pathlib.Path(filename).read_text().splitlines()[1:]:
  fields=line.split();address,port_hex=fields[1].split(":");port=int(port_hex,16)
  if fields[3]=="0A" and port in wanted:
   rows.append({"port":port,"loopback":address==("00000000000000000000000001000000" if v6 else "0100007F")})
print(json.dumps(rows))
'''
        result["listeners"] = json.loads(
            a100.checked(client, f"{q(python)} -B -c {q(port_code)}")
        )
        cron_cli = project + "/scripts/openclaw/openclaw_cli.sh"
        rc, output, _error = a100.run(client, f"{q(cron_cli)} cron list --json", timeout=180)
        if rc == 0:
            try:
                raw = json.loads(output)
                jobs = raw.get("jobs", raw if isinstance(raw, list) else [])
                result["openclaw_cron"] = {
                    "available": True,
                    "job_count": len(jobs),
                    "enabled_count": sum(
                        bool(item.get("enabled", True))
                        for item in jobs
                        if isinstance(item, dict)
                    ),
                }
            except (TypeError, json.JSONDecodeError):
                result["openclaw_cron"] = {"available": False, "reason": "invalid_json"}
        else:
            result["openclaw_cron"] = {"available": False, "reason": "cli_error"}
        print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
    finally:
        client.close()


def snapshot_action() -> None:
    client, _gate = a100.connect()
    try:
        _root, project = a100.paths(client)
        sftp = client.open_sftp()
        copied: list[str] = []
        missing: list[str] = []
        try:
            for relative in SNAPSHOT_FILES:
                try:
                    with sftp.file(project + "/" + relative, "rb") as remote:
                        data = remote.read()
                except OSError:
                    missing.append(relative)
                    continue
                target = SNAPSHOT_ROOT / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                copied.append(relative)
        finally:
            sftp.close()
        print(
            json.dumps(
                {
                    "snapshot_root": str(SNAPSHOT_ROOT),
                    "copied_count": len(copied),
                    "missing": missing,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        client.close()


def upload_action() -> None:
    client, _gate = a100.connect()
    try:
        _root, project = a100.paths(client)
        sftp = client.open_sftp()
        uploaded: list[str] = []
        try:
            for relative in P1_9_FILES:
                source = LOCAL_ROOT / "remote_edit" / "p1_9_work" / relative
                if not source.is_file():
                    raise FileNotFoundError(relative)
                remote = project + "/" + relative
                parent = remote.rsplit("/", 1)[0]
                a100.checked(client, f"mkdir -p {q(parent)}")
                with sftp.file(remote, "wb") as handle:
                    handle.write(source.read_bytes())
                mode = 0o755 if relative.startswith("scripts/") else 0o644
                sftp.chmod(remote, mode)
                uploaded.append(relative)
        finally:
            sftp.close()
        print(json.dumps({"uploaded_count": len(uploaded), "paths": uploaded}, ensure_ascii=False, indent=2))
    finally:
        client.close()


def upload_whitespace_fixes_action() -> None:
    paths = (
        "scripts/p1_9_demo/_common.sh",
        "scripts/p1_9_demo/run_dashboard.sh",
        "scripts/p1_9_demo/run_promotion_worker.sh",
        "scripts/p1_9_demo/stop_demo.sh",
    )
    client, _gate = a100.connect()
    try:
        _root, project = a100.paths(client)
        sftp = client.open_sftp()
        try:
            for relative in paths:
                source = LOCAL_ROOT / "remote_edit" / "p1_9_work" / relative
                remote_path = project + "/" + relative
                with sftp.file(remote_path, "wb") as remote:
                    remote.write(source.read_bytes())
                sftp.chmod(remote_path, 0o755)
        finally:
            sftp.close()
        print(json.dumps({"uploaded_count": len(paths)}))
    finally:
        client.close()


def formal_migration_action() -> None:
    client, _gate = a100.connect()
    try:
        root, project = a100.paths(client)
        python = root + "/conda/envs/epidemic-core/bin/python"
        code = r'''import hashlib,json,os,pathlib,subprocess
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import make_url
project=pathlib.Path(os.environ["PROJECT"])
pg_bin=project.parent/"conda/envs/postgresql18/bin"
url=make_url(os.environ["MIGRATION_DATABASE_URL"]).set(
 username="epidemicdb",password=os.environ["POSTGRES_ADMIN_PASSWORD"])
formal_db=url.database
if not formal_db or "p1_9" in formal_db: raise SystemExit("FORMAL_DATABASE_NAME_INVALID")
engine=sa.create_engine(url,hide_parameters=True,pool_pre_ping=True)
tables=("data_sources","query_requests","retrieval_runs","source_fetches","raw_artifacts","normalized_observations","quality_assessments","dataset_snapshots","dataset_snapshot_observations")
def state():
 with engine.connect() as db:
  return {
   "migration":db.execute(text("SELECT version_num FROM epidemic.alembic_version")).scalar_one(),
   "counts":{name:db.execute(text(f"SELECT count(*) FROM epidemic.{name}")).scalar_one() for name in tables},
   "snapshots":[dict(row) for row in db.execute(text("SELECT id,content_sha256,row_count FROM epidemic.dataset_snapshots ORDER BY created_at,id")).mappings()],
   "datasus_decisions":db.execute(text("SELECT count(*) FROM epidemic_discovery.source_review_decisions d JOIN epidemic_discovery.source_review_cases rc ON rc.id=d.review_case_id JOIN epidemic_discovery.source_candidate_versions cv ON cv.id=rc.candidate_version_id WHERE cv.candidate_id=CAST('5faf41ca-def4-49f2-bc4a-4cccbb81b909' AS uuid)")).scalar_one(),
   "datasus_promotions":db.execute(text("SELECT count(*) FROM epidemic_discovery.source_promotion_runs p JOIN epidemic_discovery.source_review_decisions d ON d.id=p.review_decision_id JOIN epidemic_discovery.source_review_cases rc ON rc.id=d.review_case_id JOIN epidemic_discovery.source_candidate_versions cv ON cv.id=rc.candidate_version_id WHERE cv.candidate_id=CAST('5faf41ca-def4-49f2-bc4a-4cccbb81b909' AS uuid)")).scalar_one(),
  }
before=state()
if before["migration"]!="0015_p1_8b_reviewer_read_views": raise SystemExit("FORMAL_MIGRATION_BASELINE_DRIFT")
backup_dir=project/"storage/database/postgresql18/backups/p1_9"
backup_dir.mkdir(parents=True,exist_ok=True);backup_dir.chmod(0o700)
backup=backup_dir/"pre_0016_schema.dump"
env=os.environ.copy();env["PGPASSWORD"]=url.password or ""
host=url.host or "127.0.0.1";port=str(url.port or 5432);user=url.username or "epidemicdb"
subprocess.run([str(pg_bin/"pg_dump"),"-h",host,"-p",port,"-U",user,"-d",formal_db,"--schema-only","--no-owner","--no-privileges","--format=custom","--file",str(backup)],env=env,check=True,capture_output=True)
backup.chmod(0o600)
backup_sha=hashlib.sha256(backup.read_bytes()).hexdigest()
admin=sa.create_engine(url.set(database="postgres"),isolation_level="AUTOCOMMIT",hide_parameters=True)
restore_name="epidemic_p1_9_restore_check"
with admin.connect() as db:
 db.execute(text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=:name AND pid<>pg_backend_pid()"),{"name":restore_name})
 db.exec_driver_sql(f'DROP DATABASE IF EXISTS "{restore_name}"')
 db.exec_driver_sql(f'CREATE DATABASE "{restore_name}" TEMPLATE template0')
check_url=url.set(database=restore_name)
check_env=env.copy();check_env["MIGRATION_DATABASE_URL"]=check_url.render_as_string(hide_password=False)
try:
 subprocess.run([str(pg_bin/"pg_restore"),"-h",host,"-p",port,"-U",user,"-d",restore_name,"--no-owner","--no-privileges",str(backup)],env=env,check=True,capture_output=True)
 check_engine=sa.create_engine(check_url,hide_parameters=True)
 with check_engine.begin() as db:
  db.execute(text("INSERT INTO epidemic.alembic_version(version_num) VALUES('0015_p1_8b_reviewer_read_views')"))
 subprocess.run([os.environ["PYTHON"],"-m","alembic","-c",str(project/"alembic.ini"),"upgrade","head"],cwd=project,env=check_env,check=True,capture_output=True)
 subprocess.run([os.environ["PYTHON"],"-m","alembic","-c",str(project/"alembic.ini"),"downgrade","0015_p1_8b_reviewer_read_views"],cwd=project,env=check_env,check=True,capture_output=True)
 subprocess.run([os.environ["PYTHON"],"-m","alembic","-c",str(project/"alembic.ini"),"upgrade","head"],cwd=project,env=check_env,check=True,capture_output=True)
 with check_engine.connect() as db:
  restored=db.execute(text("SELECT version_num FROM epidemic.alembic_version")).scalar_one()
  objects=db.execute(text("SELECT count(*) FROM information_schema.tables WHERE table_schema IN ('epidemic','epidemic_discovery')")).scalar_one()
 if restored!="0016_p1_9_reviewed_promotion" or objects<20: raise SystemExit("RESTORE_VERIFICATION_FAILED")
finally:
 with admin.connect() as db:
  db.execute(text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=:name AND pid<>pg_backend_pid()"),{"name":restore_name})
  db.exec_driver_sql(f'DROP DATABASE IF EXISTS "{restore_name}"')
migration_env=os.environ.copy();migration_env["MIGRATION_DATABASE_URL"]=url.render_as_string(hide_password=False)
heads=subprocess.run([os.environ["PYTHON"],"-m","alembic","-c",str(project/"alembic.ini"),"heads"],cwd=project,env=migration_env,check=True,capture_output=True,text=True).stdout.splitlines()
if len([line for line in heads if line.strip()])!=1: raise SystemExit("ALEMBIC_MULTIPLE_HEADS")
subprocess.run([os.environ["PYTHON"],"-m","alembic","-c",str(project/"alembic.ini"),"upgrade","head"],cwd=project,env=migration_env,check=True,capture_output=True)
after=state()
if after["migration"]!="0016_p1_9_reviewed_promotion": raise SystemExit("FORMAL_MIGRATION_FAILED")
if before["counts"]!=after["counts"] or before["snapshots"]!=after["snapshots"] or after["datasus_decisions"]!=0 or after["datasus_promotions"]!=0:
 raise SystemExit("FORMAL_BUSINESS_STATE_MUTATED")
print(json.dumps({"backup_sha256":backup_sha,"restore_verified":True,"single_head":True,"before":before,"after":after},default=str))
'''
        setup = (
            f"source {q(project + '/scripts/database/_env.sh')}; "
            f"set -a; source {q(project + '/.secrets/postgres.env')}; set +a; "
            f"export PROJECT={q(project)} PYTHON={q(python)}"
        )
        result = remote_json(client, python, setup, code, timeout=1200)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    finally:
        client.close()


def migration_probe_action() -> None:
    client, _gate = a100.connect()
    try:
        root, project = a100.paths(client)
        python = root + "/conda/envs/epidemic-core/bin/python"
        code = r'''import json,os,pathlib
import sqlalchemy as sa
from sqlalchemy import text
engine=sa.create_engine(os.environ["MIGRATION_DATABASE_URL"],hide_parameters=True)
result={}
with engine.connect() as db:
 result["database"]=db.execute(text("SELECT current_database()" )).scalar_one()
 for name,sql in {
  "decisions":"SELECT count(*) FROM epidemic_discovery.source_review_decisions d JOIN epidemic_discovery.source_review_cases rc ON rc.id=d.review_case_id JOIN epidemic_discovery.source_candidate_versions cv ON cv.id=rc.candidate_version_id WHERE cv.candidate_id=CAST('5faf41ca-def4-49f2-bc4a-4cccbb81b909' AS uuid)",
  "promotions":"SELECT count(*) FROM epidemic_discovery.source_promotion_runs p JOIN epidemic_discovery.source_review_decisions d ON d.id=p.review_decision_id JOIN epidemic_discovery.source_review_cases rc ON rc.id=d.review_case_id JOIN epidemic_discovery.source_candidate_versions cv ON cv.id=rc.candidate_version_id WHERE cv.candidate_id=CAST('5faf41ca-def4-49f2-bc4a-4cccbb81b909' AS uuid)",
 }.items():
  try: result[name]=db.execute(text(sql)).scalar_one()
  except Exception as exc: result[name]={"error_type":type(exc).__name__,"sqlstate":getattr(getattr(exc,"orig",None),"sqlstate",None),"message":str(getattr(exc,"orig",exc))[:300]}
print(json.dumps(result))
'''
        result = remote_json(client, python, f"source {q(project + '/scripts/database/_env.sh')}", code)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        client.close()


def setup_demo_action() -> None:
    client, _gate = a100.connect()
    try:
        root, project = a100.paths(client)
        python = root + "/conda/envs/epidemic-core/bin/python"
        code = r'''import hashlib,json,os,pathlib,secrets,shutil,subprocess,urllib.parse
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import make_url
project=pathlib.Path(os.environ["PROJECT"]);root=project.parent
pg_bin=root/"conda/envs/postgresql18/bin";demo_root=project/"storage/p1_9_demo"
pg_data=demo_root/"postgresql18/data";pg_run=demo_root/"postgresql18/run";log_root=demo_root/"log"
secret_dir=demo_root/"secrets";secret_file=project/".secrets/p1_9_demo.env"
for path in (demo_root,pg_data.parent,pg_run,log_root,secret_dir,demo_root/"quarantine/sha256",demo_root/"raw/sha256",demo_root/"run"):
 path.mkdir(parents=True,exist_ok=True);path.chmod(0o700)
if (pg_data/"PG_VERSION").exists():
 if secret_file.exists(): raise SystemExit("P1_9_DEMO_CLUSTER_ALREADY_EXISTS")
 resolved=pg_data.resolve()
 if demo_root.resolve() not in resolved.parents or "p1_9_demo" not in str(resolved):
  raise SystemExit("P1_9_INCOMPLETE_CLUSTER_PATH_INVALID")
 subprocess.run([str(pg_bin/"pg_ctl"),"-D",str(pg_data),"-m","immediate","stop"],check=False,capture_output=True)
 shutil.rmtree(pg_data);pg_data.mkdir(mode=0o700)
demo_admin_password=secrets.token_urlsafe(36);app_password=secrets.token_urlsafe(36)
review_password=secrets.token_urlsafe(36);dashboard_password=secrets.token_urlsafe(36)
pwfile=secret_dir/"initdb.pw";pwfile.write_text(demo_admin_password+"\n");pwfile.chmod(0o600)
if not (pg_data/"PG_VERSION").exists():
 subprocess.run([str(pg_bin/"initdb"),"-D",str(pg_data),"--username=p1_9_demo_admin",f"--pwfile={pwfile}","--auth-local=scram-sha-256","--auth-host=scram-sha-256","--encoding=UTF8","--locale=C"],check=True,capture_output=True)
else:
 raise SystemExit("P1_9_DEMO_CLUSTER_ALREADY_EXISTS")
pwfile.unlink()
subprocess.run([str(pg_bin/"pg_ctl"),"-D",str(pg_data),"-l",str(log_root/"postgresql.log"),"-o",f"-p 15432 -h 127.0.0.1 -k {pg_run}","start"],check=True,capture_output=True)
admin_base=sa.URL.create("postgresql+psycopg",username="p1_9_demo_admin",password=demo_admin_password,host="127.0.0.1",port=15432,database="postgres")
role_engine=sa.create_engine(admin_base,hide_parameters=True)
with role_engine.begin() as db:
 for role,password in (("p1_9_demo_app",app_password),("p1_9_demo_review",review_password),("p1_9_demo_dashboard",dashboard_password)):
  db.execute(text("SELECT set_config('p1_9.bootstrap_password',:password,true)"),{"password":password})
  db.exec_driver_sql(f"DO $$BEGIN EXECUTE format('CREATE ROLE \"{role}\" LOGIN PASSWORD %%L',current_setting('p1_9.bootstrap_password'));END$$")
admin=sa.create_engine(admin_base,isolation_level="AUTOCOMMIT",hide_parameters=True)
with admin.connect() as db:
 db.exec_driver_sql('CREATE DATABASE "epidemic_p1_9_demo" OWNER "p1_9_demo_admin" TEMPLATE template0')
formal=make_url(os.environ["MIGRATION_DATABASE_URL"]).set(username="epidemicdb",password=os.environ["POSTGRES_ADMIN_PASSWORD"])
dump=demo_root/"formal_clone.dump";formal_env=os.environ.copy();formal_env["PGPASSWORD"]=formal.password or ""
subprocess.run([str(pg_bin/"pg_dump"),"-h",formal.host or "127.0.0.1","-p",str(formal.port or 5432),"-U",formal.username or "epidemicdb","-d",formal.database or "epidemic_openclaw","--format=custom","--no-owner","--no-privileges","--file",str(dump)],env=formal_env,check=True,capture_output=True)
demo_env=os.environ.copy();demo_env["PGPASSWORD"]=demo_admin_password
subprocess.run([str(pg_bin/"pg_restore"),"-h","127.0.0.1","-p","15432","-U","p1_9_demo_admin","-d","epidemic_p1_9_demo","--no-owner","--no-privileges",str(dump)],env=demo_env,check=True,capture_output=True)
dump.unlink()
demo_url=admin_base.set(database="epidemic_p1_9_demo")
engine=sa.create_engine(demo_url,hide_parameters=True)
with engine.begin() as db:
 db.execute(text("INSERT INTO epidemic_discovery.p1_9_environment_markers(environment_type,disposable,production_write_forbidden,created_for,database_name) VALUES('p1_9_demo',true,true,'P1-9',current_database())"))
 for role in ("p1_9_demo_app","p1_9_demo_review","p1_9_demo_dashboard"):
  db.exec_driver_sql(f'GRANT CONNECT ON DATABASE "epidemic_p1_9_demo" TO "{role}"')
  db.exec_driver_sql(f'GRANT USAGE ON SCHEMA epidemic,epidemic_discovery TO "{role}"')
 db.exec_driver_sql('GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA epidemic,epidemic_discovery TO "p1_9_demo_app"')
 db.exec_driver_sql('GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA epidemic,epidemic_discovery TO "p1_9_demo_app"')
 db.exec_driver_sql('GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA epidemic_discovery TO "p1_9_demo_app"')
 db.exec_driver_sql('GRANT SELECT ON ALL TABLES IN SCHEMA epidemic,epidemic_discovery TO "p1_9_demo_review"')
 db.exec_driver_sql('GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA epidemic_discovery TO "p1_9_demo_review"')
 db.exec_driver_sql('GRANT SELECT ON ALL TABLES IN SCHEMA epidemic,epidemic_discovery TO "p1_9_demo_dashboard"')
 db.exec_driver_sql('ALTER ROLE "p1_9_demo_dashboard" SET default_transaction_read_only=on')
 marker=db.execute(text("SELECT disposable AND production_write_forbidden AND database_name=current_database() FROM epidemic_discovery.p1_9_environment_markers WHERE environment_type='p1_9_demo'" )).scalar_one()
 migration=db.execute(text("SELECT version_num FROM epidemic.alembic_version" )).scalar_one()
 if marker is not True or migration!="0017_p1_9_crypto_resolution": raise SystemExit("P1_9_DEMO_MARKER_OR_SCHEMA_INVALID")
 artifact_rows=list(db.execute(text("SELECT DISTINCT storage_key,sha256 FROM epidemic_discovery.quarantine_artifacts ORDER BY storage_key")).mappings())
formal_quarantine=project/"storage/quarantine/sha256";demo_quarantine=demo_root/"quarantine/sha256"
artifact_copies=0
for row in artifact_rows:
 key=str(row["storage_key"]);expected=str(row["sha256"])
 if len(key)!=64 or key!=expected or any(ch not in "0123456789abcdef" for ch in key):
  raise SystemExit("P1_9_ARTIFACT_STORAGE_KEY_INVALID")
 source=(formal_quarantine/key[:2]/key).resolve();target=(demo_quarantine/key[:2]/key).resolve()
 if formal_quarantine.resolve() not in source.parents or demo_quarantine.resolve() not in target.parents:
  raise SystemExit("P1_9_ARTIFACT_PATH_INVALID")
 if not source.is_file():
  continue
 payload=source.read_bytes()
 if hashlib.sha256(payload).hexdigest()!=expected:
  raise SystemExit("P1_9_FORMAL_ARTIFACT_HASH_MISMATCH")
 target.parent.mkdir(parents=True,exist_ok=True,mode=0o700);target.write_bytes(payload);target.chmod(0o600)
 artifact_copies+=1
if artifact_copies<1: raise SystemExit("P1_9_NO_VERIFIED_ARTIFACT_COPIED")
def url(role,password):
 return sa.URL.create("postgresql+psycopg",username=role,password=password,host="127.0.0.1",port=15432,database="epidemic_p1_9_demo").render_as_string(hide_password=False)
secret_file.write_text("\n".join((
 "DEMO_APP_DATABASE_URL="+urllib.parse.quote(url("p1_9_demo_app",app_password),safe=":/@+%?=&"),
 "DEMO_REVIEW_DATABASE_URL="+urllib.parse.quote(url("p1_9_demo_review",review_password),safe=":/@+%?=&"),
 "DEMO_DASHBOARD_DATABASE_URL="+urllib.parse.quote(url("p1_9_demo_dashboard",dashboard_password),safe=":/@+%?=&"),
 "DEMO_ADMIN_DATABASE_URL="+urllib.parse.quote(url("p1_9_demo_admin",demo_admin_password),safe=":/@+%?=&"),
))+"\n");secret_file.chmod(0o600)
formal_auth=project.parent/"epidemic-source-review-runtime/secrets/login_hash.json"
if not formal_auth.is_file(): raise SystemExit("FORMAL_REVIEW_AUTH_MISSING")
shutil.copyfile(formal_auth,secret_dir/"login_hash.json");(secret_dir/"login_hash.json").chmod(0o600)
print(json.dumps({"cluster":"initialized","database_marker":True,"migration":migration,"roles":3,"formal_clone":True,"paths_isolated":True,"verified_artifact_copies":artifact_copies}))
'''
        setup = (
            f"source {q(project + '/scripts/database/_env.sh')}; "
            f"set -a; source {q(project + '/.secrets/postgres.env')}; set +a; "
            f"export PROJECT={q(project)}"
        )
        result = remote_json(client, python, setup, code, timeout=1200)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        client.close()


def reset_demo_action() -> None:
    client, _gate = a100.connect()
    try:
        root, project = a100.paths(client)
        python = root + "/conda/envs/epidemic-core/bin/python"
        verify_code = r'''import json,os,pathlib
import sqlalchemy as sa
from sqlalchemy import text
project=pathlib.Path(os.environ["PROJECT"]).resolve();demo_root=(project/"storage/p1_9_demo").resolve()
if demo_root != (project/"storage/p1_9_demo").resolve() or demo_root.name!="p1_9_demo": raise SystemExit("P1_9_RESET_PATH_INVALID")
engine=sa.create_engine(os.environ["DEMO_ADMIN_DATABASE_URL"],hide_parameters=True)
with engine.connect() as db:
 name=db.execute(text("SELECT current_database()")).scalar_one()
 marker=db.execute(text("SELECT disposable AND production_write_forbidden AND created_for='P1-9' AND database_name=current_database() FROM epidemic_discovery.p1_9_environment_markers WHERE environment_type='p1_9_demo'")).scalar_one_or_none()
if name!="epidemic_p1_9_demo" or marker is not True: raise SystemExit("P1_9_RESET_MARKER_INVALID")
print(json.dumps({"database_name_verified":True,"marker_verified":True,"path_verified":True}))
'''
        setup = (
            f"set -a; source {q(project + '/.secrets/p1_9_demo.env')}; set +a; "
            f"export PROJECT={q(project)}"
        )
        verified = remote_json(client, python, setup, verify_code, timeout=120)
        a100.checked(client, q(project + "/scripts/p1_9_demo/stop_demo.sh"), timeout=180)
        cleanup_code = r'''import json,os,pathlib,shutil
project=pathlib.Path(os.environ["PROJECT"]).resolve();demo_root=(project/"storage/p1_9_demo").resolve()
if demo_root != (project/"storage/p1_9_demo").resolve() or demo_root.name!="p1_9_demo": raise SystemExit("P1_9_RESET_PATH_INVALID")
if demo_root.is_dir(): shutil.rmtree(demo_root)
secret_file=project/".secrets/p1_9_demo.env"
if secret_file.exists(): secret_file.unlink()
print(json.dumps({"demo_storage_removed":True,"demo_secret_removed":True}))
'''
        cleaned = remote_json(client, python, f"export PROJECT={q(project)}", cleanup_code, timeout=180)
        print(json.dumps({"verified": verified, "cleaned": cleaned}, ensure_ascii=False, indent=2))
    finally:
        client.close()
    setup_demo_action()
    configure_demo_auth_action()
    start_demo_action()
    stage_demo_login_action()


def start_demo_action() -> None:
    client, _gate = a100.connect()
    try:
        _root, project = a100.paths(client)
        output = a100.checked(client, q(project + "/scripts/p1_9_demo/start_demo.sh"), timeout=180)
        print(json.dumps({"started": True, "details": output[-1000:]}))
    finally:
        client.close()


def restart_demo_action() -> None:
    client, _gate = a100.connect()
    try:
        _root, project = a100.paths(client)
        a100.checked(client, q(project + "/scripts/p1_9_demo/stop_demo.sh"), timeout=180)
        output = a100.checked(client, q(project + "/scripts/p1_9_demo/start_demo.sh"), timeout=180)
        print(json.dumps({"restarted": True, "details": output[-1000:]}))
    finally:
        client.close()


def configure_demo_auth_action() -> None:
    client, _gate = a100.connect()
    try:
        root, project = a100.paths(client)
        python = root + "/conda/envs/epidemic-core/bin/python"
        code = r'''import hashlib,json,os,pathlib,secrets
project=pathlib.Path(os.environ["PROJECT"]);secret_dir=project/"storage/p1_9_demo/secrets"
secret_dir.mkdir(parents=True,exist_ok=True);secret_dir.chmod(0o700)
username="p1_9_demo_"+secrets.token_hex(6);password=secrets.token_urlsafe(32);salt=secrets.token_bytes(16)
n,r,p=16384,8,1;digest=hashlib.scrypt(password.encode(),salt=salt,n=n,r=r,p=p,dklen=32)
auth={"username":username,"salt_hex":salt.hex(),"hash_hex":digest.hex(),"n":n,"r":r,"p":p}
runtime={"username":username,"password":password}
for name,value in (("login_hash.json",auth),("login_runtime.json",runtime)):
 path=secret_dir/name;path.write_text(json.dumps(value));path.chmod(0o600)
print(json.dumps({"configured":True,"credential_values_exposed":False}))
'''
        result = remote_json(client, python, f"export PROJECT={q(project)}", code)
        a100.run(client, q(project + "/scripts/p1_9_demo/stop_demo.sh"), timeout=120)
        a100.checked(client, q(project + "/scripts/p1_9_demo/start_demo.sh"), timeout=180)
        print(json.dumps(result))
    finally:
        client.close()


def copy_demo_field_action(field: str) -> None:
    client, _gate = a100.connect()
    try:
        _root, project = a100.paths(client)
        sftp = client.open_sftp()
        try:
            with sftp.file(project + "/storage/p1_9_demo/secrets/login_runtime.json", "rb") as handle:
                value = json.loads(handle.read().decode("utf-8"))[field]
        finally:
            sftp.close()
    finally:
        client.close()
    process = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", "$input | Set-Clipboard"],
        input=str(value),
        text=True,
        capture_output=True,
        check=True,
    )
    if process.returncode != 0:
        raise SystemExit("CLIPBOARD_WRITE_FAILED")
    print(json.dumps({"copied": field, "value_exposed": False}))


def stage_demo_login_action() -> None:
    client, _gate = a100.connect()
    try:
        _root, project = a100.paths(client)
        sftp = client.open_sftp()
        try:
            with sftp.file(project + "/storage/p1_9_demo/secrets/login_runtime.json", "rb") as handle:
                payload = handle.read()
        finally:
            sftp.close()
    finally:
        client.close()
    target = Path(tempfile.gettempdir()) / "epidemic_p1_9_demo_login_runtime.json"
    target.write_bytes(payload)
    print(json.dumps({"staged": True, "path": str(target), "values_exposed": False}))


def demo_diagnostics_action() -> None:
    client, _gate = a100.connect()
    try:
        root, project = a100.paths(client)
        sftp = client.open_sftp()
        result: dict[str, Any] = {}
        try:
            for name in ("review", "promotion", "discovery", "dashboard"):
                path = project + f"/storage/p1_9_demo/log/{name}.log"
                try:
                    with sftp.file(path, "rb") as handle:
                        handle.seek(max(0, handle.stat().st_size - 6000))
                        value = handle.read().decode("utf-8", "replace")
                except OSError:
                    value = "LOG_UNAVAILABLE"
                value = re.sub(r"postgresql(?:\+\w+)?://[^\s]+", "[DATABASE_URL_REDACTED]", value)
                value = re.sub(r"(?i)(password|token|secret|api[_-]?key)=\S+", r"\1=[REDACTED]", value)
                result[name] = value[-4000:]
        finally:
            sftp.close()
        python = root + "/conda/envs/epidemic-core/bin/python"
        code = r'''import json,os,pathlib
import sqlalchemy as sa
from sqlalchemy import text
engine=sa.create_engine(os.environ["DEMO_ADMIN_DATABASE_URL"],hide_parameters=True)
with engine.connect() as db:
 row=db.execute(text("SELECT p.id,p.state,p.attempt_count,p.max_attempts,p.last_error_code,ar.storage_key,ar.sha256 FROM epidemic_discovery.source_promotion_runs p JOIN epidemic_discovery.source_review_decisions d ON d.id=p.review_decision_id JOIN epidemic_discovery.source_review_cases rc ON rc.id=d.review_case_id JOIN epidemic_discovery.quarantine_artifacts ar ON ar.candidate_version_id=rc.candidate_version_id ORDER BY p.created_at DESC LIMIT 1")).mappings().one_or_none()
 discovery=db.execute(text("SELECT id,state,created_at,updated_at FROM epidemic_discovery.discovery_runs ORDER BY created_at DESC LIMIT 1")).mappings().one_or_none()
 events=[] if discovery is None else [dict(item) for item in db.execute(text("SELECT event_code,to_state,details,created_at FROM epidemic_discovery.discovery_run_events WHERE discovery_run_id=:id ORDER BY created_at DESC LIMIT 4"),{"id":discovery["id"]}).mappings()]
artifact_check=None
if row:
 root=pathlib.Path(os.environ["PROJECT"])/"storage/p1_9_demo/quarantine/sha256";key=str(row["storage_key"])
 expected=root/key[:2]/key
 alternatives=[item for item in root.rglob(str(row["sha256"])) if item.is_file()]
 formal_root=pathlib.Path(os.environ["PROJECT"])/"storage/quarantine/sha256"
 formal_matches=[item for item in formal_root.rglob(str(row["sha256"])) if item.is_file()]
 artifact_check={"storage_key":key,"expected_exists":expected.is_file(),"matching_files_in_demo_root":len(alternatives),"matching_relative_paths":[str(item.relative_to(root)) for item in alternatives[:3]],"matching_files_in_formal_root":len(formal_matches)}
print(json.dumps({"latest_promotion":dict(row) if row else None,"latest_artifact_check":artifact_check,"latest_discovery":dict(discovery) if discovery else None,"latest_discovery_events":events},default=str))
'''
        setup = f"set -a; source {q(project + '/.secrets/p1_9_demo.env')}; set +a; export PROJECT={q(project)}"
        result.update(remote_json(client, python, setup, code, timeout=120))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        client.close()


def apply_hotfix_action() -> None:
    client, _gate = a100.connect()
    try:
        root, project = a100.paths(client)
        python = root + "/conda/envs/epidemic-core/bin/python"
        a100.run(client, q(project + "/scripts/p1_9_demo/stop_demo.sh"), timeout=120)
        a100.checked(client, q(project + "/scripts/p1_9_demo/start_demo.sh"), timeout=180)
        code = r'''import json,os,pathlib,subprocess
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import make_url
project=pathlib.Path(os.environ["PROJECT"])
formal_url=make_url(os.environ["MIGRATION_DATABASE_URL"]).set(username="epidemicdb",password=os.environ["POSTGRES_ADMIN_PASSWORD"])
demo_url=make_url(os.environ["DEMO_ADMIN_DATABASE_URL"])
tables=("data_sources","query_requests","retrieval_runs","source_fetches","raw_artifacts","normalized_observations","quality_assessments","dataset_snapshots","dataset_snapshot_observations")
def state(url,formal):
 engine=sa.create_engine(url,hide_parameters=True)
 with engine.connect() as db:
  result={"migration":db.execute(text("SELECT version_num FROM epidemic.alembic_version")).scalar_one(),"counts":{name:db.execute(text(f"SELECT count(*) FROM epidemic.{name}")).scalar_one() for name in tables},"snapshots":[dict(row) for row in db.execute(text("SELECT id,content_sha256,row_count FROM epidemic.dataset_snapshots ORDER BY created_at,id")).mappings()]}
  if formal:
   result["decisions"]=db.execute(text("SELECT count(*) FROM epidemic_discovery.source_review_decisions d JOIN epidemic_discovery.source_review_cases rc ON rc.id=d.review_case_id JOIN epidemic_discovery.source_candidate_versions cv ON cv.id=rc.candidate_version_id WHERE cv.candidate_id=CAST('5faf41ca-def4-49f2-bc4a-4cccbb81b909' AS uuid)" )).scalar_one()
   result["promotions"]=db.execute(text("SELECT count(*) FROM epidemic_discovery.source_promotion_runs p JOIN epidemic_discovery.source_review_decisions d ON d.id=p.review_decision_id JOIN epidemic_discovery.source_review_cases rc ON rc.id=d.review_case_id JOIN epidemic_discovery.source_candidate_versions cv ON cv.id=rc.candidate_version_id WHERE cv.candidate_id=CAST('5faf41ca-def4-49f2-bc4a-4cccbb81b909' AS uuid)" )).scalar_one()
 return result
formal_before=state(formal_url,True);demo_before=state(demo_url,False)
if formal_before["migration"]!="0016_p1_9_reviewed_promotion" or demo_before["migration"]!="0016_p1_9_reviewed_promotion": raise SystemExit("P1_9_HOTFIX_BASELINE_INVALID")
for url in (formal_url,demo_url):
 env=os.environ.copy();env["MIGRATION_DATABASE_URL"]=url.render_as_string(hide_password=False)
 subprocess.run([os.environ["PYTHON"],"-m","alembic","-c",str(project/"alembic.ini"),"upgrade","head"],cwd=project,env=env,check=True,capture_output=True)
demo_env=os.environ.copy();demo_env["MIGRATION_DATABASE_URL"]=demo_url.render_as_string(hide_password=False)
subprocess.run([os.environ["PYTHON"],"-m","alembic","-c",str(project/"alembic.ini"),"downgrade","0016_p1_9_reviewed_promotion"],cwd=project,env=demo_env,check=True,capture_output=True)
subprocess.run([os.environ["PYTHON"],"-m","alembic","-c",str(project/"alembic.ini"),"upgrade","head"],cwd=project,env=demo_env,check=True,capture_output=True)
formal_after=state(formal_url,True);demo_after=state(demo_url,False)
if formal_after["migration"]!="0017_p1_9_crypto_resolution" or demo_after["migration"]!="0017_p1_9_crypto_resolution": raise SystemExit("P1_9_HOTFIX_UPGRADE_FAILED")
for before,after in ((formal_before,formal_after),(demo_before,demo_after)):
 if before["counts"]!=after["counts"] or before["snapshots"]!=after["snapshots"]: raise SystemExit("P1_9_HOTFIX_BUSINESS_STATE_MUTATED")
if formal_after["decisions"]!=0 or formal_after["promotions"]!=0: raise SystemExit("P1_9_FORMAL_REVIEW_STATE_MUTATED")
print(json.dumps({"formal":formal_after,"demo":demo_after,"demo_downgrade_reupgrade":True},default=str))
'''
        setup = (
            f"source {q(project + '/scripts/database/_env.sh')}; "
            f"set -a; source {q(project + '/.secrets/postgres.env')}; "
            f"source {q(project + '/.secrets/p1_9_demo.env')}; set +a; "
            f"export PROJECT={q(project)} PYTHON={q(python)}"
        )
        result = remote_json(client, python, setup, code, timeout=600)
        a100.checked(client, q(project + "/scripts/p1_9_demo/start_demo.sh"), timeout=180)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        client.close()


def hotfix_diagnostics_action() -> None:
    client, _gate = a100.connect()
    try:
        root, project = a100.paths(client)
        python = root + "/conda/envs/epidemic-core/bin/python"
        code = r'''import json,os,pathlib,subprocess
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import make_url
project=pathlib.Path(os.environ["PROJECT"])
url=make_url(os.environ["MIGRATION_DATABASE_URL"]).set(username="epidemicdb",password=os.environ["POSTGRES_ADMIN_PASSWORD"])
engine=sa.create_engine(url,hide_parameters=True)
with engine.connect() as db:
 rows=[dict(row) for row in db.execute(text("SELECT e.extname,n.nspname AS schema FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace WHERE e.extname='pgcrypto'")).mappings()]
 version=db.execute(text("SELECT version_num FROM epidemic.alembic_version")).scalar_one()
env=os.environ.copy();env["MIGRATION_DATABASE_URL"]=url.render_as_string(hide_password=False)
p=subprocess.run([os.environ["PYTHON"],"-m","alembic","-c",str(project/"alembic.ini"),"upgrade","head"],cwd=project,env=env,capture_output=True,text=True)
error=(p.stdout+"\n"+p.stderr)
error=error.replace(url.render_as_string(hide_password=False),"[DATABASE_URL_REDACTED]")
print(json.dumps({"pgcrypto":rows,"version_before":version,"returncode":p.returncode,"alembic_tail":error[-5000:]}))
'''
        setup = (
            f"source {q(project + '/scripts/database/_env.sh')}; "
            f"set -a; source {q(project + '/.secrets/postgres.env')}; set +a; "
            f"export PROJECT={q(project)} PYTHON={q(python)}"
        )
        result = remote_json(client, python, setup, code, timeout=600)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        client.close()


def safe_fetcher_probe_action() -> None:
    client, _gate = a100.connect()
    try:
        _root, project = a100.paths(client)
        command = (
            f"cd {q(project)} && grep -R -n -E --include='*.py' "
            f"{q('class SafeFetcher|DISCOVERY.*TIMEOUT|timeout_seconds|connect_timeout|read_timeout')} src/epidemic_core; "
            "grep -n -E 'DISCOVERY_QUARANTINE_ROOT|QUARANTINE_ROOT|PROJECT=' "
            "scripts/source_discovery_worker/_common.sh scripts/p1_9_demo/_common.sh scripts/p1_9_demo/run_discovery_worker.sh"
        )
        rc, output, error = a100.run(client, command, timeout=120)
        print(json.dumps({"returncode": rc, "matches": (output + error)[-12000:]}, ensure_ascii=False, indent=2))
    finally:
        client.close()


def relocate_misplaced_demo_artifact_action() -> None:
    client, _gate = a100.connect()
    try:
        root, project = a100.paths(client)
        python = root + "/conda/envs/epidemic-core/bin/python"
        code = r'''import hashlib,json,os,pathlib
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import make_url
project=pathlib.Path(os.environ["PROJECT"]).resolve()
demo=sa.create_engine(os.environ["DEMO_ADMIN_DATABASE_URL"],hide_parameters=True)
formal_url=make_url(os.environ["MIGRATION_DATABASE_URL"]).set(username="epidemicdb",password=os.environ["POSTGRES_ADMIN_PASSWORD"])
formal=sa.create_engine(formal_url,hide_parameters=True)
with demo.connect() as db:
 marker=db.execute(text("SELECT disposable AND production_write_forbidden AND created_for='P1-9' AND database_name=current_database() FROM epidemic_discovery.p1_9_environment_markers WHERE environment_type='p1_9_demo'")).scalar_one_or_none()
 row=db.execute(text("SELECT ar.storage_key,ar.sha256 FROM epidemic_discovery.source_promotion_runs p JOIN epidemic_discovery.source_review_decisions d ON d.id=p.review_decision_id JOIN epidemic_discovery.source_review_cases rc ON rc.id=d.review_case_id JOIN epidemic_discovery.quarantine_artifacts ar ON ar.candidate_version_id=rc.candidate_version_id ORDER BY p.created_at DESC LIMIT 1")).mappings().one()
if marker is not True: raise SystemExit("P1_9_RELOCATE_MARKER_INVALID")
key=str(row["storage_key"]);expected=str(row["sha256"])
if key!=expected or len(key)!=64 or any(ch not in "0123456789abcdef" for ch in key): raise SystemExit("P1_9_RELOCATE_KEY_INVALID")
with formal.connect() as db:
 refs=db.execute(text("SELECT (SELECT count(*) FROM epidemic_discovery.quarantine_artifacts WHERE sha256=:sha)+(SELECT count(*) FROM epidemic.raw_artifacts WHERE sha256=:sha)"),{"sha":expected}).scalar_one()
if refs!=0: raise SystemExit("P1_9_FORMAL_ARTIFACT_STILL_REFERENCED")
formal_root=(project/"storage/quarantine/sha256").resolve();demo_root=(project/"storage/p1_9_demo/quarantine/sha256").resolve()
source=(formal_root/key[:2]/key).resolve();target=(demo_root/key[:2]/key).resolve()
if formal_root not in source.parents or demo_root not in target.parents: raise SystemExit("P1_9_RELOCATE_PATH_INVALID")
payload=source.read_bytes()
if hashlib.sha256(payload).hexdigest()!=expected: raise SystemExit("P1_9_RELOCATE_HASH_INVALID")
target.parent.mkdir(parents=True,exist_ok=True,mode=0o700);target.write_bytes(payload);target.chmod(0o600)
if hashlib.sha256(target.read_bytes()).hexdigest()!=expected: raise SystemExit("P1_9_RELOCATE_COPY_INVALID")
source.unlink()
try: source.parent.rmdir()
except OSError: pass
print(json.dumps({"marker_verified":True,"formal_reference_count":refs,"hash_verified":True,"copied_to_demo":True,"orphan_removed_from_formal":True}))
'''
        setup = (
            f"source {q(project + '/scripts/database/_env.sh')}; "
            f"set -a; source {q(project + '/.secrets/postgres.env')}; "
            f"source {q(project + '/.secrets/p1_9_demo.env')}; set +a; export PROJECT={q(project)}"
        )
        result = remote_json(client, python, setup, code, timeout=180)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        client.close()


def e2e_results_action() -> None:
    client, _gate = a100.connect()
    try:
        root, project = a100.paths(client)
        python = root + "/conda/envs/epidemic-core/bin/python"
        code = r'''import hashlib,json,os,pathlib
from decimal import Decimal
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import make_url

def canonical(value):
 return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":"),default=str)
def digest(value):
 return hashlib.sha256(canonical(value).encode()).hexdigest()

formal_url=make_url(os.environ["MIGRATION_DATABASE_URL"]).set(username="epidemicdb",password=os.environ["POSTGRES_ADMIN_PASSWORD"])
formal=sa.create_engine(formal_url,hide_parameters=True,pool_pre_ping=True)
demo=sa.create_engine(os.environ["DEMO_ADMIN_DATABASE_URL"],hide_parameters=True,pool_pre_ping=True)
tables=("data_sources","query_requests","retrieval_runs","source_fetches","raw_artifacts","normalized_observations","quality_assessments","dataset_snapshots","dataset_snapshot_observations")
expected_counts={"data_sources":2,"query_requests":4,"retrieval_runs":9,"source_fetches":9,"raw_artifacts":4,"normalized_observations":358,"quality_assessments":358,"dataset_snapshots":8,"dataset_snapshot_observations":978}
expected_snapshot_hashes=["26ac6c9078678c7d6d3a8446b1860001afe6db425c1b9195bccc4d08ee94ad11","157708c70589ec3036512cc56dfee2637418bad65bd056a70e06a9265b1cd516","af4f4b2de64362812ef515ccb5c9d8a795f83cf7696cccc7b9bb6bc7675d5b54","d3d259642dad7b08dc76210e936be04c009c1f556e3c6b109595b76191ab2359","ad8a2b7c10dff2bcebf3f7fbc5fce0ed702e1d14892935b348d7bdf787967c8b","89a97c89ec72c0a3475d1be3b720643aa5ce0ca59c72f0b42feee6c1c3cb08ad","201d8c174181c5362bc3ff0a485fc2bb5856bc19d09ae9c98016529a3562c3ca","8ce65713841ec74e9be099fd571e0923295f5285b9be2c494348bb74d2c5a745"]
with formal.connect() as db:
 formal_counts={name:db.execute(text(f"SELECT count(*) FROM epidemic.{name}")).scalar_one() for name in tables}
 formal_snapshots=[dict(row) for row in db.execute(text("SELECT id,content_sha256,row_count FROM epidemic.dataset_snapshots ORDER BY created_at,id")).mappings()]
 formal_candidate=dict(db.execute(text("SELECT c.status candidate_status,rc.state review_state,(SELECT count(*) FROM epidemic_discovery.source_review_decisions d WHERE d.review_case_id=rc.id) decision_count,(SELECT count(*) FROM epidemic_discovery.source_promotion_runs p JOIN epidemic_discovery.source_review_decisions d ON d.id=p.review_decision_id WHERE d.review_case_id=rc.id) promotion_count FROM epidemic_discovery.source_candidates c JOIN epidemic_discovery.source_candidate_versions cv ON cv.candidate_id=c.id JOIN epidemic_discovery.source_review_cases rc ON rc.candidate_version_id=cv.id WHERE c.id=CAST('5faf41ca-def4-49f2-bc4a-4cccbb81b909' AS uuid)")).mappings().one())
 formal_artifact_refs=db.execute(text("SELECT (SELECT count(*) FROM epidemic_discovery.quarantine_artifacts WHERE sha256=:sha)+(SELECT count(*) FROM epidemic.raw_artifacts WHERE sha256=:sha)"),{"sha":"f77f8fb9693b14ed5683fb9933d7bef4f0d22aa990fcbb6e6f327cc53ad42f52"}).scalar_one()
 formal_migration=db.execute(text("SELECT version_num FROM epidemic.alembic_version")).scalar_one()

with demo.connect() as db:
 publication=dict(db.execute(text("""SELECT pub.id publication_id,pub.promotion_run_id,pub.data_source_id,pub.source_version_id,pub.normalization_run_id,pub.snapshot_id,pub.dashboard_path,pub.observation_count,pub.publication_sha256,p.state promotion_state,p.attempt_count,d.id approval_id,d.test_only,d.production_usable,d.environment decision_environment,rc.id review_case_id,cv.id candidate_version_id,cv.candidate_id,c.discovery_run_id,ar.sha256 artifact_sha256,mc.id mapping_contract_id,mc.mapping_sha256,mc.metric_code,mc.unit,mc.data_nature,mc.training_eligible,mc.national_aggregate,mc.source_date_semantics,mc.geography_semantics,ds.source_key,ds.display_name,ds.authority,s.content_sha256 snapshot_sha256,s.row_count snapshot_row_count
 FROM epidemic_discovery.p1_9_publications pub
 JOIN epidemic_discovery.source_promotion_runs p ON p.id=pub.promotion_run_id
 JOIN epidemic_discovery.source_review_decisions d ON d.id=p.review_decision_id
 JOIN epidemic_discovery.source_review_cases rc ON rc.id=d.review_case_id
 JOIN epidemic_discovery.source_candidate_versions cv ON cv.id=rc.candidate_version_id
 JOIN epidemic_discovery.source_candidates c ON c.id=cv.candidate_id
 JOIN epidemic_discovery.quarantine_artifacts ar ON ar.candidate_version_id=cv.id
 JOIN epidemic_discovery.p1_9_source_versions sv ON sv.id=pub.source_version_id
 JOIN epidemic_discovery.p1_9_mapping_contracts mc ON mc.id=sv.mapping_contract_id
 JOIN epidemic.data_sources ds ON ds.id=pub.data_source_id
 JOIN epidemic.dataset_snapshots s ON s.id=pub.snapshot_id
 ORDER BY pub.published_at DESC LIMIT 1""")).mappings().one())
 observations=[dict(row) for row in db.execute(text("""SELECT o.id,o.record_fingerprint,o.period_start,o.value_numeric,o.dimensions,o.dimensions_sha256,l.source_row_set_sha256,l.source_row_count,l.artifact_sha256,l.mapping_contract_id,l.candidate_version_id,qa.status quality_status
 FROM epidemic.dataset_snapshot_observations so
 JOIN epidemic.normalized_observations o ON o.id=so.observation_id
 JOIN epidemic_discovery.p1_9_observation_lineage l ON l.observation_id=o.id
 JOIN epidemic.quality_assessments qa ON qa.observation_id=o.id
 WHERE so.snapshot_id=:snapshot ORDER BY so.row_number"""),{"snapshot":publication["snapshot_id"]}).mappings()]
 source_ids=[str(row[0]) for row in db.execute(text("SELECT DISTINCT sf.source_id FROM epidemic.dataset_snapshot_observations so JOIN epidemic.normalized_observations o ON o.id=so.observation_id JOIN epidemic.source_fetches sf ON sf.id=o.source_fetch_id WHERE so.snapshot_id=:snapshot"),{"snapshot":publication["snapshot_id"]})]
 lineage_samples=[]
 for row in observations[:3]:
  lineage_samples.append({"observation_id":str(row["id"]),"promotion_run_id":str(publication["promotion_run_id"]),"mapping_contract_id":str(row["mapping_contract_id"]),"candidate_version_id":str(row["candidate_version_id"]),"artifact_sha256":row["artifact_sha256"],"source_row_set_sha256":row["source_row_set_sha256"],"source_row_count":row["source_row_count"]})
 marker=db.execute(text("SELECT disposable AND production_write_forbidden AND created_for='P1-9' AND database_name=current_database() FROM epidemic_discovery.p1_9_environment_markers WHERE environment_type='p1_9_demo'")).scalar_one()

material=[]
date_totals={};municipality_totals={};all_dimension_keys=set();total=Decimal(0)
for row in observations:
 value=row["value_numeric"];value_int=int(value) if Decimal(value)==Decimal(value).to_integral() else str(value)
 material.append({"record_fingerprint":row["record_fingerprint"],"period_start":row["period_start"].isoformat(),"value":value_int,"dimensions_sha256":row["dimensions_sha256"]})
 total+=Decimal(value)
 date=row["period_start"].isoformat();municipality=str((row["dimensions"] or {}).get("municipality_code"))
 date_totals[date]=date_totals.get(date,0)+value_int
 municipality_totals[municipality]=municipality_totals.get(municipality,0)+value_int
 all_dimension_keys.update((row["dimensions"] or {}).keys())
recomputed=digest(sorted(material,key=lambda item:str(item["record_fingerprint"])))
demo_root=pathlib.Path(os.environ["PROJECT"])/"storage/p1_9_demo/quarantine/sha256"
formal_root=pathlib.Path(os.environ["PROJECT"])/"storage/quarantine/sha256"
artifact=publication["artifact_sha256"]
demo_artifact=demo_root/artifact[:2]/artifact;formal_artifact=formal_root/artifact[:2]/artifact
dashboard_engine=sa.create_engine(os.environ["DEMO_DASHBOARD_DATABASE_URL"],hide_parameters=True)
with dashboard_engine.connect() as db:
 dashboard_permissions={"role":db.execute(text("SELECT current_user")).scalar_one(),"transaction_read_only":db.execute(text("SHOW default_transaction_read_only")).scalar_one(),"observation_insert":db.execute(text("SELECT has_table_privilege(current_user,'epidemic.normalized_observations','INSERT')")).scalar_one(),"snapshot_insert":db.execute(text("SELECT has_table_privilege(current_user,'epidemic.dataset_snapshots','INSERT')")).scalar_one()}

result={
 "formal":{"migration":formal_migration,"counts":formal_counts,"counts_match":formal_counts==expected_counts,"snapshots":formal_snapshots,"snapshot_hashes_match":[row["content_sha256"] for row in formal_snapshots]==expected_snapshot_hashes,"candidate":formal_candidate,"demo_artifact_db_refs":formal_artifact_refs,"demo_artifact_file_exists":formal_artifact.exists()},
 "demo":{"marker_verified":marker is True,"publication":publication,"observation_count":len(observations),"value_total":str(total),"quality_count":sum(1 for row in observations if row["quality_status"]=="accepted"),"source_ids":source_ids,"source_isolation":len(source_ids)==1 and source_ids[0]==str(publication["data_source_id"]),"date_total":sum(date_totals.values()),"municipality_total":sum(municipality_totals.values()),"empty_time_filter_count":len(observations),"snapshot_hash_recomputed":recomputed,"snapshot_hash_matches":recomputed==publication["snapshot_sha256"],"allowed_dimension_keys":sorted(all_dimension_keys),"sensitive_dimensions_absent":all_dimension_keys<={"country","source_key","state_code","data_nature","name_status","quality_status","municipality_code","source_version_id","training_eligible","national_aggregate","geography_semantics"},"artifact_exists":demo_artifact.is_file(),"artifact_hash_matches":demo_artifact.is_file() and hashlib.sha256(demo_artifact.read_bytes()).hexdigest()==artifact,"lineage_samples":lineage_samples,"dashboard_permissions":dashboard_permissions},
 "pass": formal_migration=="0017_p1_9_crypto_resolution" and formal_counts==expected_counts and [row["content_sha256"] for row in formal_snapshots]==expected_snapshot_hashes and formal_candidate=={"candidate_status":"REVIEW_READY","review_state":"REVIEW_READY","decision_count":0,"promotion_count":0} and formal_artifact_refs==0 and not formal_artifact.exists() and marker is True and publication["promotion_state"]=="PUBLISHED" and publication["test_only"] is True and publication["production_usable"] is False and len(observations)>0 and len(source_ids)==1 and source_ids[0]==str(publication["data_source_id"]) and recomputed==publication["snapshot_sha256"] and demo_artifact.is_file() and hashlib.sha256(demo_artifact.read_bytes()).hexdigest()==artifact and len(lineage_samples)==3 and all_dimension_keys<={"country","source_key","state_code","data_nature","name_status","quality_status","municipality_code","source_version_id","training_eligible","national_aggregate","geography_semantics"} and dashboard_permissions["transaction_read_only"]=="on" and not dashboard_permissions["observation_insert"] and not dashboard_permissions["snapshot_insert"]
}
print(json.dumps(result,default=str,ensure_ascii=False))
'''
        setup = (
            f"source {q(project + '/scripts/database/_env.sh')}; "
            f"set -a; source {q(project + '/.secrets/postgres.env')}; "
            f"source {q(project + '/.secrets/p1_9_demo.env')}; set +a; "
            f"export PROJECT={q(project)}"
        )
        result = remote_json(client, python, setup, code, timeout=600)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        if not result.get("pass"):
            raise SystemExit(1)
    finally:
        client.close()


def integration_tests_action() -> None:
    client, _gate = a100.connect()
    try:
        root, project = a100.paths(client)
        python = root + "/conda/envs/epidemic-core/bin/python"
        stop = (
            f"source {q(project + '/scripts/p1_9_demo/_common.sh')}; "
            "pidfile=$RUN_ROOT/promotion.pid; "
            "if [[ -f $pidfile ]]; then pid=$(<\"$pidfile\"); "
            "if [[ $pid =~ ^[0-9]+$ ]] && kill -0 $pid 2>/dev/null; then kill $pid; "
            "for _ in {1..50}; do kill -0 $pid 2>/dev/null || break; sleep 0.1; done; fi; rm -f \"$pidfile\"; fi"
        )
        a100.checked(client, f"bash -c {q(stop)}", timeout=120)
        code = r'''import concurrent.futures,datetime as dt,json,os,uuid
import sqlalchemy as sa
from sqlalchemy import text
from epidemic_core.p1_9.promotion import EnvironmentGate,_transition,claim_one,process_one,sha

engine=sa.create_engine(os.environ["DEMO_ADMIN_DATABASE_URL"],hide_parameters=True,pool_pre_ping=True)
gate=EnvironmentGate.verify(engine)
old_candidate="5faf41ca-def4-49f2-bc4a-4cccbb81b909"
with engine.connect() as db:
 card=dict(db.execute(text("""SELECT rc.id review_case_id,rc.etag,cv.id candidate_version_id,cv.candidate_id,c.discovery_run_id,ar.sha256 artifact_sha256,sp.schema_sha256,mp.mapping_sha256,cv.terms_sha256
 FROM epidemic_discovery.source_candidates c JOIN epidemic_discovery.source_candidate_versions cv ON cv.candidate_id=c.id JOIN epidemic_discovery.source_review_cases rc ON rc.candidate_version_id=cv.id JOIN epidemic_discovery.quarantine_artifacts ar ON ar.candidate_version_id=cv.id JOIN epidemic_discovery.source_schema_profiles sp ON sp.candidate_version_id=cv.id JOIN epidemic_discovery.source_mapping_proposals mp ON mp.candidate_version_id=cv.id AND mp.revision=rc.current_mapping_revision WHERE c.id=CAST(:candidate AS uuid)"""),{"candidate":old_candidate}).mappings().one())
binding={key:str(card[key]) for key in ("candidate_id","candidate_version_id","discovery_run_id","review_case_id","artifact_sha256","schema_sha256","mapping_sha256")};binding["license_evidence_sha256"]=str(card["terms_sha256"])
binding_sha=sha(binding);decision_key=uuid.uuid4()
confirmations={"license":True,"geography":True,"disease":True,"mapping":True,"metric_unit":True,"time_semantics":True,"second_confirmation":True}
geography={"country":"Brazil","national_aggregate":False,"geography_semantics":"notification_location","scope_status":"sample_only"}
metric={"metric_code":"notification_record_count","unit":"notifications","data_nature":"derived_aggregation","training_eligible":False,"national_aggregate":False,"source_date_semantics":"notification_date"}
review_sql=text("""SELECT epidemic_discovery.p1_9_submit_review_decision(CAST(:case AS uuid),'APPROVED',:reviewer,:comment,CAST(:key AS uuid),CAST(:etag AS char(64)),'p1_9_demo','integration_test',true,CAST(:confirmations AS jsonb),CAST(:geography AS jsonb),CAST(:metric AS jsonb),CAST(:binding AS jsonb),CAST(:binding_sha AS char(64)))""")
params={"case":str(card["review_case_id"]),"reviewer":"p1_9_integration_test","comment":"P1-9 concurrent review boundary validation","key":str(decision_key),"etag":str(card["etag"]),"confirmations":json.dumps(confirmations),"geography":json.dumps(geography),"metric":json.dumps(metric),"binding":json.dumps(binding),"binding_sha":binding_sha}
def submit_review(_):
 try:
  with engine.begin() as db:return {"ok":True,"id":str(db.execute(review_sql,params).scalar_one())}
 except Exception as exc:return {"ok":False,"sqlstate":getattr(getattr(exc,"orig",None),"sqlstate",None)}
with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:review_calls=list(pool.map(submit_review,range(10)))
with engine.connect() as db:
 decisions=[str(row[0]) for row in db.execute(text("SELECT id FROM epidemic_discovery.source_review_decisions WHERE idempotency_key=:key"),{"key":decision_key})]
decision_id=decisions[0]

promotion_key=sha({"decision":decision_id,"environment":"p1_9_demo"})
queue_sql=text("SELECT epidemic_discovery.p1_9_queue_promotion(CAST(:decision AS uuid),'p1_9_demo',CAST(:key AS char(64)))")
def queue(_):
 with engine.begin() as db:return str(db.execute(queue_sql,{"decision":decision_id,"key":promotion_key}).scalar_one())
with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:promotion_calls=list(pool.map(queue,range(10)))
job_id=promotion_calls[0]
with engine.connect() as db:job_count=db.execute(text("SELECT count(*) FROM epidemic_discovery.source_promotion_runs WHERE review_decision_id=CAST(:id AS uuid)"),{"id":decision_id}).scalar_one()

unapproved_rejected=False
try:
 with engine.begin() as db:db.execute(queue_sql,{"decision":str(uuid.uuid4()),"key":"0"*64}).scalar_one()
except Exception:unapproved_rejected=True

with engine.connect() as db:
 main=dict(db.execute(text("SELECT p.id,d.id decision_id,pub.snapshot_id FROM epidemic_discovery.p1_9_publications pub JOIN epidemic_discovery.source_promotion_runs p ON p.id=pub.promotion_run_id JOIN epidemic_discovery.source_review_decisions d ON d.id=p.review_decision_id ORDER BY pub.published_at DESC LIMIT 1")).mappings().one())
 before_duplicate=db.execute(text("SELECT count(*) FROM epidemic.dataset_snapshot_observations WHERE snapshot_id=:snapshot"),{"snapshot":main["snapshot_id"]}).scalar_one()
with engine.begin() as db:duplicate_id=str(db.execute(queue_sql,{"decision":str(main["decision_id"]),"key":sha({"repeat":str(main["decision_id"])})}).scalar_one())
with engine.connect() as db:after_duplicate=db.execute(text("SELECT count(*) FROM epidemic.dataset_snapshot_observations WHERE snapshot_id=:snapshot"),{"snapshot":main["snapshot_id"]}).scalar_one()

old_token=uuid.uuid4()
with engine.begin() as db:db.execute(text("UPDATE epidemic_discovery.source_promotion_runs SET state='CLAIMED',claim_token=:token,claim_owner='lease-test',lease_expires_at=now()+interval '5 minutes',attempt_count=0,next_attempt_at=NULL WHERE id=CAST(:id AS uuid)"),{"id":job_id,"token":old_token})
future_claim=claim_one(engine,gate,"future-lease-test")
with engine.begin() as db:db.execute(text("UPDATE epidemic_discovery.source_promotion_runs SET lease_expires_at=now()-interval '1 second' WHERE id=CAST(:id AS uuid)"),{"id":job_id})
recovered=claim_one(engine,gate,"expired-lease-test")
stale_rejected=False
try:_transition(engine,uuid.UUID(job_id),old_token,"PUBLISHING","STALE_COMPLETION_TEST")
except RuntimeError as exc:stale_rejected=str(exc)=="STALE_CLAIM"

count_sql={"data_sources":"SELECT count(*) FROM epidemic.data_sources","raw_artifacts":"SELECT count(*) FROM epidemic.raw_artifacts","normalized_observations":"SELECT count(*) FROM epidemic.normalized_observations","quality_assessments":"SELECT count(*) FROM epidemic.quality_assessments","dataset_snapshots":"SELECT count(*) FROM epidemic.dataset_snapshots","snapshot_members":"SELECT count(*) FROM epidemic.dataset_snapshot_observations","mapping_contracts":"SELECT count(*) FROM epidemic_discovery.p1_9_mapping_contracts","source_versions":"SELECT count(*) FROM epidemic_discovery.p1_9_source_versions","publications":"SELECT count(*) FROM epidemic_discovery.p1_9_publications"}
def core_counts():
 with engine.connect() as db:return {name:db.execute(text(sql)).scalar_one() for name,sql in count_sql.items()}
faults=[]
for phase in ("staging","quality","normalization","snapshot","publication"):
 with engine.begin() as db:
  db.execute(text("DELETE FROM epidemic_discovery.p1_9_staging_observations WHERE promotion_run_id=CAST(:id AS uuid)"),{"id":job_id})
  db.execute(text("UPDATE epidemic_discovery.source_promotion_runs SET state='PROMOTION_QUEUED',claim_token=NULL,claim_owner=NULL,lease_expires_at=NULL,attempt_count=0,next_attempt_at=NULL,last_error_code=NULL WHERE id=CAST(:id AS uuid)"),{"id":job_id})
 before=core_counts();os.environ["P1_9_FAULT_PHASE"]=phase
 try:claimed=process_one(engine,gate,"fault-test-"+phase)
 finally:os.environ.pop("P1_9_FAULT_PHASE",None)
 after=core_counts()
 with engine.connect() as db:
  state=db.execute(text("SELECT state FROM epidemic_discovery.source_promotion_runs WHERE id=CAST(:id AS uuid)"),{"id":job_id}).scalar_one()
 faults.append({"phase":phase,"claimed":claimed,"state":state,"core_counts_unchanged":before==after})
with engine.begin() as db:
 db.execute(text("DELETE FROM epidemic_discovery.p1_9_staging_observations WHERE promotion_run_id=CAST(:id AS uuid)"),{"id":job_id})
 db.execute(text("UPDATE epidemic_discovery.source_promotion_runs SET state='FAILED_TERMINAL',claim_token=NULL,claim_owner=NULL,lease_expires_at=NULL,next_attempt_at=NULL,last_error_code='INTEGRATION_TEST_COMPLETE' WHERE id=CAST(:id AS uuid)"),{"id":job_id})
with engine.connect() as db:
 active_publication=str(db.execute(text("SELECT id FROM epidemic_discovery.p1_9_publications ORDER BY published_at DESC LIMIT 1")).scalar_one())

result={"review_concurrency":{"calls":10,"successes":sum(row["ok"] for row in review_calls),"errors":sum(not row["ok"] for row in review_calls),"decision_rows":len(decisions)},"promotion_concurrency":{"calls":10,"unique_job_ids":len(set(promotion_calls)),"job_rows":job_count},"unapproved_promotion_rejected":unapproved_rejected,"duplicate_promotion":{"same_job":duplicate_id==str(main["id"]),"observation_count_unchanged":before_duplicate==after_duplicate},"lease_recovery":{"future_lease_not_claimed":future_claim is None,"expired_lease_reclaimed":recovered is not None and str(recovered[0])==job_id,"stale_claim_rejected":stale_rejected},"fault_injection":faults,"old_publication_remains_active":active_publication is not None}
result["pass"]=result["review_concurrency"]=={"calls":10,"successes":1,"errors":9,"decision_rows":1} and result["promotion_concurrency"]=={"calls":10,"unique_job_ids":1,"job_rows":1} and unapproved_rejected and result["duplicate_promotion"]["same_job"] and result["duplicate_promotion"]["observation_count_unchanged"] and all(result["lease_recovery"].values()) and all(row["claimed"] and row["state"]=="FAILED_RETRYABLE" and row["core_counts_unchanged"] for row in faults) and result["old_publication_remains_active"]
print(json.dumps(result))
'''
        setup = (
            f"source {q(project + '/scripts/p1_9_demo/_common.sh')}; "
            "export APP_DATABASE_URL=\"$DEMO_ADMIN_DATABASE_URL\"; "
            f"export PROJECT={q(project)}"
        )
        try:
            result = remote_json(client, python, setup, code, timeout=1200)
        finally:
            a100.checked(client, q(project + "/scripts/p1_9_demo/start_demo.sh"), timeout=180)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        if not result.get("pass"):
            raise SystemExit(1)
    finally:
        client.close()


def deploy_formal_runtime_action() -> None:
    client, _gate = a100.connect()
    try:
        _root, project = a100.paths(client)
        results: dict[str, Any] = {}
        for name, relative in (
            ("review_stop", "scripts/source_review/stop_source_review.sh"),
            ("promotion_stop", "scripts/source_promotion_worker/stop_source_promotion_worker.sh"),
            ("review_runtime_install", "scripts/source_review/install_runtime.sh"),
            ("review_start", "scripts/source_review/start_source_review.sh"),
            ("promotion_start", "scripts/source_promotion_worker/start_source_promotion_worker.sh"),
        ):
            rc, output, error = a100.run(client, q(project + "/" + relative), timeout=180)
            results[name] = {"returncode": rc, "tail": (output + error)[-800:]}
            if rc != 0:
                raise SystemExit(name + " failed")
        route_status = a100.checked(
            client,
            "curl --silent --show-error --noproxy '*' --max-time 8 --output /dev/null --write-out '%{http_code}' http://127.0.0.1:8021/p1-9/reviews",
            timeout=30,
        )
        checks = {
            "review_status": a100.run(client, q(project + "/scripts/source_review/status_source_review.sh"), timeout=120)[0] == 0,
            "promotion_status": a100.run(client, q(project + "/scripts/source_promotion_worker/status_source_promotion_worker.sh"), timeout=120)[0] == 0,
            "review_route": route_status in {"200", "302", "303"},
        }
        print(json.dumps({"steps": results, "checks": checks, "review_route_http_status": route_status, "pass": all(checks.values())}, ensure_ascii=False, indent=2))
        if not all(checks.values()):
            raise SystemExit(1)
    finally:
        client.close()


def formal_runtime_diagnostics_action() -> None:
    client, _gate = a100.connect()
    try:
        root, project = a100.paths(client)
        sftp = client.open_sftp()
        result: dict[str, Any] = {}
        try:
            paths = {
                "review_log": project + "/../epidemic-source-review-runtime/log/review.log",
                "promotion_log": project + "/storage/logs/source-promotion-worker/worker.log",
                "review_installer": project + "/scripts/source_review/install_runtime.sh",
            }
            for name, path in paths.items():
                try:
                    with sftp.file(path, "rb") as handle:
                        size = handle.stat().st_size
                        handle.seek(max(0, size - (8000 if name.endswith("log") else 16000)))
                        value = handle.read().decode("utf-8", "replace")
                except OSError:
                    value = "UNAVAILABLE"
                value = re.sub(r"postgresql(?:\+\w+)?://[^\s]+", "[DATABASE_URL_REDACTED]", value)
                value = re.sub(r"(?i)(password|token|secret|api[_-]?key)=\S+", r"\1=[REDACTED]", value)
                value = value.replace(project, "[project]").replace(root, "[data-root]")
                result[name] = value
        finally:
            sftp.close()
        result["review_status"] = a100.run(client, q(project + "/scripts/source_review/status_source_review.sh"), timeout=120)[0]
        result["promotion_status"] = a100.run(client, q(project + "/scripts/source_promotion_worker/status_source_promotion_worker.sh"), timeout=120)[0]
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        client.close()


def stop_demo_action() -> None:
    client, _gate = a100.connect()
    try:
        _root, project = a100.paths(client)
        output = a100.checked(client, q(project + "/scripts/p1_9_demo/stop_demo.sh"), timeout=180)
        print(json.dumps({"stopped": True, "data_removed": False, "details": output[-1000:]}))
    finally:
        client.close()


def ensure_demo_action() -> None:
    client, _gate = a100.connect()
    try:
        _root, project = a100.paths(client)
        configured = a100.run(
            client,
            f"test -r {q(project + '/.secrets/p1_9_demo.env')} -a -r {q(project + '/storage/p1_9_demo/secrets/login_hash.json')}",
            timeout=30,
        )[0] == 0
    finally:
        client.close()
    if not configured:
        setup_demo_action()
        configure_demo_auth_action()
    start_demo_action()
    stage_demo_login_action()


def formal_immutability_check_action() -> None:
    client, _gate = a100.connect()
    try:
        root, project = a100.paths(client)
        python = root + "/conda/envs/epidemic-core/bin/python"
        code = r'''import json,os
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import make_url
expected_counts={"data_sources":2,"query_requests":4,"retrieval_runs":9,"source_fetches":9,"raw_artifacts":4,"normalized_observations":358,"quality_assessments":358,"dataset_snapshots":8,"dataset_snapshot_observations":978}
expected_hashes=["26ac6c9078678c7d6d3a8446b1860001afe6db425c1b9195bccc4d08ee94ad11","157708c70589ec3036512cc56dfee2637418bad65bd056a70e06a9265b1cd516","af4f4b2de64362812ef515ccb5c9d8a795f83cf7696cccc7b9bb6bc7675d5b54","d3d259642dad7b08dc76210e936be04c009c1f556e3c6b109595b76191ab2359","ad8a2b7c10dff2bcebf3f7fbc5fce0ed702e1d14892935b348d7bdf787967c8b","89a97c89ec72c0a3475d1be3b720643aa5ce0ca59c72f0b42feee6c1c3cb08ad","201d8c174181c5362bc3ff0a485fc2bb5856bc19d09ae9c98016529a3562c3ca","8ce65713841ec74e9be099fd571e0923295f5285b9be2c494348bb74d2c5a745"]
url=make_url(os.environ["MIGRATION_DATABASE_URL"]).set(username="epidemicdb",password=os.environ["POSTGRES_ADMIN_PASSWORD"]);engine=sa.create_engine(url,hide_parameters=True)
tables=tuple(expected_counts)
with engine.connect() as db:
 counts={name:db.execute(text(f"SELECT count(*) FROM epidemic.{name}")).scalar_one() for name in tables};hashes=[row[0] for row in db.execute(text("SELECT content_sha256 FROM epidemic.dataset_snapshots ORDER BY created_at,id"))];candidate=dict(db.execute(text("SELECT c.status candidate_status,rc.state review_state,(SELECT count(*) FROM epidemic_discovery.source_review_decisions d WHERE d.review_case_id=rc.id) decision_count,(SELECT count(*) FROM epidemic_discovery.source_promotion_runs p JOIN epidemic_discovery.source_review_decisions d ON d.id=p.review_decision_id WHERE d.review_case_id=rc.id) promotion_count FROM epidemic_discovery.source_candidates c JOIN epidemic_discovery.source_candidate_versions cv ON cv.candidate_id=c.id JOIN epidemic_discovery.source_review_cases rc ON rc.candidate_version_id=cv.id WHERE c.id=CAST('5faf41ca-def4-49f2-bc4a-4cccbb81b909' AS uuid)")).mappings().one());migration=db.execute(text("SELECT version_num FROM epidemic.alembic_version")).scalar_one()
passed=counts==expected_counts and hashes==expected_hashes and candidate=={"candidate_status":"REVIEW_READY","review_state":"REVIEW_READY","decision_count":0,"promotion_count":0} and migration=="0017_p1_9_crypto_resolution"
print(json.dumps({"counts":counts,"snapshot_count":len(hashes),"snapshot_hashes_match":hashes==expected_hashes,"candidate":candidate,"migration":migration,"pass":passed}))
'''
        setup = (
            f"source {q(project + '/scripts/database/_env.sh')}; "
            f"set -a; source {q(project + '/.secrets/postgres.env')}; set +a"
        )
        result = remote_json(client, python, setup, code, timeout=300)
        services = {
            "postgres": a100.run(client, q(project + "/scripts/postgres/status.sh"), timeout=120)[0] == 0,
            "review": a100.run(client, q(project + "/scripts/source_review/status_source_review.sh"), timeout=120)[0] == 0,
            "promotion": a100.run(client, q(project + "/scripts/source_promotion_worker/status_source_promotion_worker.sh"), timeout=120)[0] == 0,
        }
        result["services"] = services
        result["pass"] = bool(result["pass"] and all(services.values()))
        print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
        if not result["pass"]:
            raise SystemExit(1)
    finally:
        client.close()


def cleanup_demo_action() -> None:
    client, _gate = a100.connect()
    try:
        root, project = a100.paths(client)
        python = root + "/conda/envs/epidemic-core/bin/python"
        code = r'''import json,os,pathlib,shutil
import sqlalchemy as sa
from sqlalchemy import text
project=pathlib.Path(os.environ["PROJECT"]).resolve();root=(project/"storage/p1_9_demo").resolve()
if root.name!="p1_9_demo" or root!=(project/"storage/p1_9_demo").resolve():raise SystemExit("P1_9_CLEANUP_PATH_INVALID")
engine=sa.create_engine(os.environ["DEMO_ADMIN_DATABASE_URL"],hide_parameters=True)
with engine.connect() as db:
 name=db.execute(text("SELECT current_database()")).scalar_one()
 marker=db.execute(text("SELECT disposable AND production_write_forbidden AND created_for='P1-9' AND database_name=current_database() FROM epidemic_discovery.p1_9_environment_markers WHERE environment_type='p1_9_demo'")).scalar_one_or_none()
if name!="epidemic_p1_9_demo" or marker is not True:raise SystemExit("P1_9_CLEANUP_MARKER_INVALID")
print(json.dumps({"database_name_verified":True,"marker_verified":True,"path_verified":True}))
'''
        setup = f"source {q(project + '/scripts/p1_9_demo/_common.sh')}; export PROJECT={q(project)}"
        verified = remote_json(client, python, setup, code, timeout=120)
        a100.checked(client, q(project + "/scripts/p1_9_demo/stop_demo.sh"), timeout=180)
        clean = r'''import json,os,pathlib,shutil
project=pathlib.Path(os.environ["PROJECT"]).resolve();root=(project/"storage/p1_9_demo").resolve()
if root.name!="p1_9_demo" or root!=(project/"storage/p1_9_demo").resolve():raise SystemExit("P1_9_CLEANUP_PATH_INVALID")
if root.exists():shutil.rmtree(root)
secret=project/".secrets/p1_9_demo.env"
if secret.exists():secret.unlink()
print(json.dumps({"demo_storage_removed":True,"demo_secret_removed":True}))
'''
        cleaned = remote_json(client, python, f"export PROJECT={q(project)}", clean, timeout=180)
        print(json.dumps({"verified": verified, "cleaned": cleaned}, ensure_ascii=False, indent=2))
    finally:
        client.close()


def full_validation_action() -> None:
    client, _gate = a100.connect()
    try:
        root, project = a100.paths(client)
        python = root + "/conda/envs/epidemic-core/bin/python"
        cli = project + "/scripts/openclaw/openclaw_cli.sh"

        def checked_command(label: str, command: str, timeout: int) -> dict[str, object]:
            started = time.monotonic()
            rc, output, error = a100.run(client, f"bash -c {q(command)}", timeout=timeout)
            safe = (output + "\n" + error).replace(project, "[project]").replace(root, "[data-root]")
            safe = re.sub(r"postgresql(?:\+\w+)?://[^\s]+", "[DATABASE_URL_REDACTED]", safe)
            safe = re.sub(r"(?i)(password|token|secret|api[_-]?key)(\s*[=:]\s*)\S+", r"\1\2[REDACTED]", safe)
            safe = re.sub(r"https?://\S+", "[URL_REDACTED]", safe)
            return {
                "label": label,
                "returncode": rc,
                "duration_seconds": round(time.monotonic() - started, 3),
                "output_sha256": hashlib.sha256(safe.encode()).hexdigest(),
                "tail": safe.splitlines()[-30:],
            }

        npm = project + "/runtime/node-v24.18.0-linux-x64/bin/npm"
        node_bin = project + "/runtime/node-v24.18.0-linux-x64/bin"
        npm_dir = project + "/integrations/openclaw/epidemic-query-tools"
        npm_prefix = f"export PATH={q(node_bin)}:$PATH; cd {q(npm_dir)}"
        checks = [
            checked_command("pytest_p1_9_unit", f"cd {q(project)}; PYTHONPATH=src {q(python)} -m pytest -p no:cacheprovider -q tests/unit/test_p1_9_promotion.py tests/unit/test_p1_9_ui.py", 900),
            checked_command("ruff_check", f"cd {q(project)}; {q(python)} -m ruff check src tests migrations", 1200),
            checked_command("ruff_format", f"cd {q(project)}; {q(python)} -m ruff format --check src tests migrations", 1200),
            checked_command("mypy_strict", f"cd {q(project)}; {q(python)} -m mypy --strict src", 1800),
            checked_command("pip_check", f"cd {q(project)}; {q(python)} -m pip check", 600),
            checked_command("bash_syntax", f"bash -n {q(project + '/scripts/p1_9_demo/')}*.sh {q(project + '/scripts/source_promotion_worker/_common.sh')} {q(project + '/scripts/source_promotion_worker/start_source_promotion_worker.sh')} {q(project + '/scripts/source_review/install_runtime.sh')}", 300),
            checked_command("alembic_single_head", f"cd {q(project)}; {q(python)} -m alembic -c alembic.ini heads", 600),
            checked_command("npm_build", f"{npm_prefix}; {q(npm)} run build", 1200),
            checked_command("npm_test", f"{npm_prefix}; {q(npm)} test", 1200),
            checked_command("npm_audit", f"{npm_prefix}; {q(npm)} audit --json --registry=https://registry.npmjs.org --fetch-retries=1 --fetch-timeout=60000", 1200),
            checked_command("openclaw_doctor_lint", f"{q(cli)} doctor --lint --json", 600),
            checked_command("openclaw_doctor_deep", f"{q(cli)} doctor --deep --non-interactive", 900),
            checked_command("openclaw_security_deep", f"{q(cli)} security audit --deep --json", 900),
        ]
        code = r'''import hashlib,json,os,pathlib,re,subprocess
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import make_url
project=pathlib.Path(os.environ["PROJECT"]);files=json.loads(os.environ["FILES"])
rules={"private_key":r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----","cloud_token":r"(?:AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9_]{30,}|sk-[A-Za-z0-9]{24,})","credential_url":r"(?:postgresql|https?)://[^\s:/]+:[^\s/@]+@","tls_bypass":r"curl\s+-k(?:\s|$)|wget\s+--no-check-certificate|GIT_SSL_NO_VERIFY\s*=\s*(?:1|true)|pip\s+[^\n]*--trusted-host","proxy_literal":r"(?i)(?:http|https|all)_proxy\s*=\s*['\"]?(?:https?|socks)://"}
findings=[];large=[]
for rel in files:
 path=project/rel
 if not path.is_file():continue
 if path.stat().st_size>10*1024*1024:large.append({"path":rel,"bytes":path.stat().st_size})
 content=path.read_text(errors="replace")
 for name,pattern in rules.items():
  for match in re.finditer(pattern,content,re.I|re.M):findings.append({"path":rel,"line":content.count("\n",0,match.start())+1,"rule":name,"match_sha256":hashlib.sha256(match.group().encode()).hexdigest()})
formal_url=make_url(os.environ["MIGRATION_DATABASE_URL"]).set(username="epidemicdb",password=os.environ["POSTGRES_ADMIN_PASSWORD"])
formal=sa.create_engine(formal_url,hide_parameters=True);demo=sa.create_engine(os.environ["DEMO_ADMIN_DATABASE_URL"],hide_parameters=True)
with formal.connect() as db:
 schema={"alembic":db.execute(text("SELECT version_num FROM epidemic.alembic_version")).scalar_one(),"p1_9_tables":db.execute(text("SELECT count(*) FROM information_schema.tables WHERE table_schema='epidemic_discovery' AND table_name LIKE 'p1_9_%'")).scalar_one(),"p1_9_functions":db.execute(text("SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='epidemic_discovery' AND p.proname LIKE 'p1_9_%'")).scalar_one()}
with demo.connect() as db:
 demo_schema={"alembic":db.execute(text("SELECT version_num FROM epidemic.alembic_version")).scalar_one(),"marker":db.execute(text("SELECT disposable AND production_write_forbidden AND created_for='P1-9' AND database_name=current_database() FROM epidemic_discovery.p1_9_environment_markers WHERE environment_type='p1_9_demo'")).scalar_one()}
ports={5432,8000,8010,18789,8020,8021,15432,18020,18021};listeners=[]
for filename,v6 in (("/proc/net/tcp",False),("/proc/net/tcp6",True)):
 for line in pathlib.Path(filename).read_text().splitlines()[1:]:
  fields=line.split();address,port_hex=fields[1].split(":");port=int(port_hex,16)
  if fields[3]=="0A" and port in ports:listeners.append({"port":port,"loopback":address==("00000000000000000000000001000000" if v6 else "0100007F")})
project_bytes=str(project).encode();project_processes=0;proxy_processes=0
for proc in pathlib.Path("/proc").iterdir():
 if not proc.name.isdigit():continue
 try:cmd=(proc/"cmdline").read_bytes();env=(proc/"environ").read_bytes()
 except Exception:continue
 if project_bytes not in cmd:continue
 project_processes+=1;names={item.split(b"=",1)[0] for item in env.split(b"\0") if b"=" in item}
 proxy_processes+=int(bool(names&{b"http_proxy",b"https_proxy",b"HTTP_PROXY",b"HTTPS_PROXY",b"all_proxy",b"ALL_PROXY"}))
log_hits=[];sensitive_tokens=("cpf","nm_paciente","nome_paciente","cs_sexo","nu_idade_n","dt_nasc","nome_mae","logradouro","telefone","email")
for log in (project/"storage/p1_9_demo/log").glob("*.log"):
 text_value=log.read_text(errors="replace").lower()
 for token in sensitive_tokens:
  if token in text_value:log_hits.append({"log":log.name,"token_sha256":hashlib.sha256(token.encode()).hexdigest()})
status=subprocess.run(["git","-C",str(project),"status","--porcelain=v1","-uall"],text=True,capture_output=True,check=True).stdout.splitlines()
staged=subprocess.run(["git","-C",str(project),"diff","--cached","--name-only"],text=True,capture_output=True,check=True).stdout.splitlines()
diff_check=subprocess.run(["git","-C",str(project),"diff","--check"],capture_output=True).returncode
print(json.dumps({"scan":{"findings":findings,"large_files":large,"sensitive_demo_log_hits":log_hits},"schema":{"formal":schema,"demo":demo_schema},"runtime":{"listeners":listeners,"project_processes":project_processes,"proxy_processes":proxy_processes},"git":{"dirty_count":len(status),"staged_paths":staged,"diff_check_returncode":diff_check}}))
'''
        setup = (
            f"source {q(project + '/scripts/database/_env.sh')}; "
            f"set -a; source {q(project + '/.secrets/postgres.env')}; "
            f"source {q(project + '/.secrets/p1_9_demo.env')}; set +a; "
            f"export PROJECT={q(project)} FILES={q(json.dumps(P1_9_FILES))}"
        )
        audit = remote_json(client, python, setup, code, timeout=600)
        service_paths = {
            "postgres": "scripts/postgres/status.sh",
            "vllm": "scripts/qwen_vllm/status.sh",
            "query_api": "scripts/query_api/status_query_api.sh",
            "retrieval_worker": "scripts/retrieval_worker/status_retrieval_worker.sh",
            "openclaw": "scripts/openclaw/status_openclaw.sh",
            "dashboard": "scripts/dashboard/status_dashboard.sh",
            "discovery_worker": "scripts/source_discovery_worker/status_source_discovery_worker.sh",
            "promotion_worker": "scripts/source_promotion_worker/status_source_promotion_worker.sh",
            "review_console": "scripts/source_review/status_source_review.sh",
            "daily_update": "scripts/daily_update/status_daily_update.sh",
            "p1_9_demo": "scripts/p1_9_demo/status_demo.sh",
        }
        services = {name: a100.run(client, q(project + "/" + relative), timeout=180)[0] == 0 for name, relative in service_paths.items()}
        session_proxy_absent = a100.checked(client, "bash -lc '[[ -z ${HTTP_PROXY:-}${HTTPS_PROXY:-}${ALL_PROXY:-}${http_proxy:-}${https_proxy:-}${all_proxy:-} ]] && echo yes || echo no'") == "yes"
        required_commands = {"pytest_p1_9_unit","ruff_check","ruff_format","mypy_strict","pip_check","bash_syntax","alembic_single_head","npm_build","npm_test","npm_audit"}
        commands_pass = all(row["returncode"] == 0 for row in checks if row["label"] in required_commands)
        doctor_by_name = {str(row["label"]): row for row in checks}
        openclaw_executed = all(name in doctor_by_name for name in ("openclaw_doctor_lint","openclaw_doctor_deep","openclaw_security_deep"))
        result = {"checks": checks,"audit": audit,"services": services,"new_session_proxy_absent": session_proxy_absent,"openclaw_checks_executed": openclaw_executed}
        result["pass"] = commands_pass and openclaw_executed and not audit["scan"]["findings"] and not audit["scan"]["large_files"] and not audit["scan"]["sensitive_demo_log_hits"] and audit["schema"]["formal"]["alembic"] == "0017_p1_9_crypto_resolution" and audit["schema"]["demo"]["alembic"] == "0017_p1_9_crypto_resolution" and audit["schema"]["demo"]["marker"] is True and all(item["loopback"] for item in audit["runtime"]["listeners"]) and audit["runtime"]["proxy_processes"] == 0 and audit["git"]["diff_check_returncode"] == 0 and not audit["git"]["staged_paths"] and all(services.values()) and session_proxy_absent
        print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
        if not result["pass"]:
            raise SystemExit(1)
    finally:
        client.close()


def isolated_full_test_action() -> None:
    client, _gate = a100.connect()
    try:
        root, project = a100.paths(client)
        python = root + "/conda/envs/epidemic-core/bin/python"
        code = r'''import json,os,pathlib,re,secrets,shutil,socket,subprocess,sys
import sqlalchemy as sa
from sqlalchemy.engine import URL,make_url
project=pathlib.Path(os.environ["PROJECT"]);python=os.environ["PYTHON_BIN"];pgbin=pathlib.Path(os.environ["POSTGRES_BIN"])
temp_root=pathlib.Path(os.environ["TMP_ROOT"])/("p1_9_isolated_pg_"+secrets.token_hex(6));data=temp_root/"data";log=temp_root/"postgres.log";socket_dir=pathlib.Path("/tmp")/("p19pg_"+secrets.token_hex(5))
source_urls={key:make_url(os.environ[key]) for key in ("MIGRATION_DATABASE_URL","APP_DATABASE_URL","RETRIEVAL_DATABASE_URL","NORMALIZATION_DATABASE_URL","REVIEW_DATABASE_URL","PROMOTION_DATABASE_URL") if os.environ.get(key)}
role_names={key:value.username for key,value in source_urls.items() if value.username};checks=[];started=False;result=None
def run(label,args,env,timeout):
 p=subprocess.run(args,cwd=project,env=env,text=True,capture_output=True,timeout=timeout)
 value=(p.stdout+"\n"+p.stderr).replace(str(project),"[project]").replace(str(project.parent),"[data-root]")
 value=re.sub(r"(?i)(password|token|secret|api[_-]?key)(\s*[=:]\s*)\S+",r"\1\2[redacted]",value);value=re.sub(r"(?:postgresql[^\s]+|https?://\S+)","[url-redacted]",value)
 checks.append({"name":label,"returncode":p.returncode,"tail":value.splitlines()[-35:]})
 if p.returncode:
  print(json.dumps({"failure":label,"checks":checks}),file=sys.stderr);raise RuntimeError(label+" failed")
 return p
try:
 temp_root.mkdir(parents=True,mode=0o700);socket_dir.mkdir(mode=0o700);base_env=os.environ.copy();base_env["PATH"]=str(pgbin)+os.pathsep+base_env.get("PATH","")
 run("initdb",[str(pgbin/"initdb"),"-D",str(data),"-U","postgres","--auth-local=trust","--auth-host=scram-sha-256","--no-sync"],base_env,600)
 with socket.socket() as probe:probe.bind(("127.0.0.1",0));port=probe.getsockname()[1]
 run("postgres_start",[str(pgbin/"pg_ctl"),"-D",str(data),"-l",str(log),"-o",f"-k {socket_dir} -p {port} -h 127.0.0.1 -F","-w","start"],base_env,600);started=True
 admin_url=URL.create("postgresql+psycopg",username="postgres",database="postgres",query={"host":str(socket_dir),"port":str(port)});admin=sa.create_engine(admin_url,isolation_level="AUTOCOMMIT",hide_parameters=True)
 migration_text="\n".join(path.read_text(errors="replace") for path in (project/"migrations/versions").glob("*.py"));roles=set(re.findall(r"\bepidemic[a-z0-9_]+\b",migration_text));roles.update(role_names.values());roles={role for role in roles if role not in {"epidemic","epidemicopenclaw","postgres"} and len(role)<64}
 with admin.connect() as db:
  for role in sorted(roles):db.exec_driver_sql('CREATE ROLE "'+role+'" LOGIN')
  app_login=role_names["APP_DATABASE_URL"]
  if app_login!="epidemic_app":db.exec_driver_sql('GRANT epidemic_app TO "'+app_login+'"')
  if "REVIEW_DATABASE_URL" in role_names and role_names["REVIEW_DATABASE_URL"]!="epidemicreview":db.exec_driver_sql('GRANT epidemicreview TO "'+role_names["REVIEW_DATABASE_URL"]+'"')
  db.exec_driver_sql("CREATE DATABASE epidemic_p19_test TEMPLATE template0 ENCODING 'UTF8'")
 test_migration=str(admin_url.set(database="epidemic_p19_test"));bootstrap=sa.create_engine(test_migration,isolation_level="AUTOCOMMIT",hide_parameters=True)
 with bootstrap.connect() as db:
  db.exec_driver_sql("CREATE SCHEMA epidemic");db.exec_driver_sql("GRANT USAGE ON SCHEMA epidemic TO epidemic_app")
 bootstrap.dispose()
 def role_url(role):return str(URL.create("postgresql+psycopg",username=role,database="epidemic_p19_test",query={"host":str(socket_dir),"port":str(port)}))
 app_user=role_names["APP_DATABASE_URL"];env=base_env.copy();env.update({"MIGRATION_DATABASE_URL":test_migration,"APP_DATABASE_URL":role_url(app_user),"DATABASE_URL":role_url(app_user),"PYTHONDONTWRITEBYTECODE":"1","PYTHONPATH":str(project/"src")})
 for key in ("RETRIEVAL_DATABASE_URL","NORMALIZATION_DATABASE_URL","REVIEW_DATABASE_URL","PROMOTION_DATABASE_URL"):
  if key in role_names:env[key]=role_url(role_names[key])
 run("alembic_upgrade",[python,"-m","alembic","upgrade","head"],env,900)
 run("seed_reference",[python,"-m","epidemic_core.cli","seed-reference"],env,300)
 run("pytest_full",[python,"-m","pytest","-p","no:cacheprovider","-q"],env,3600)
 run("alembic_downgrade_one",[python,"-m","alembic","downgrade","-1"],env,900)
 run("alembic_reupgrade",[python,"-m","alembic","upgrade","head"],env,900)
 run("alembic_current",[python,"-m","alembic","current"],env,300)
 run("alembic_heads",[python,"-m","alembic","heads"],env,300)
 test_engine=sa.create_engine(test_migration,hide_parameters=True)
 with test_engine.connect() as db:
  version=db.execute(sa.text("SELECT version_num FROM epidemic.alembic_version")).scalar_one();p19_tables=db.execute(sa.text("SELECT count(*) FROM information_schema.tables WHERE table_schema='epidemic_discovery' AND table_name LIKE 'p1_9_%'")).scalar_one();p19_functions=db.execute(sa.text("SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='epidemic_discovery' AND p.proname LIKE 'p1_9_%'")).scalar_one()
 test_engine.dispose()
 if version!="0017_p1_9_crypto_resolution" or p19_tables!=7 or p19_functions!=2:raise RuntimeError("isolated schema verification failed")
 result={"checks":checks,"schema":{"version":version,"p1_9_tables":p19_tables,"p1_9_functions":p19_functions,"single_head":True}}
finally:
 if started:subprocess.run([str(pgbin/"pg_ctl"),"-D",str(data),"-m","fast","-w","stop"],env=os.environ.copy(),text=True,capture_output=True,timeout=300)
 if temp_root.exists():shutil.rmtree(temp_root)
 if socket_dir.exists():shutil.rmtree(socket_dir)
if result is None:raise RuntimeError("isolated test did not produce a result")
result["temporary_cluster_removed"]=not temp_root.exists() and not socket_dir.exists();result["pass"]=all(row["returncode"]==0 for row in checks) and result["temporary_cluster_removed"] and result["schema"]["version"]=="0017_p1_9_crypto_resolution";print(json.dumps(result))
'''
        setup = (
            f"source {q(project + '/scripts/database/_env.sh')}; "
            f"export PROJECT={q(project)} PYTHON_BIN={q(python)} "
            f"POSTGRES_BIN={q(root + '/conda/envs/postgresql18/bin')} TMP_ROOT={q(project + '/storage/tmp')}"
        )
        result = remote_json(client, python, setup, code, timeout=5400)
        print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
        if not result.get("pass"):
            raise SystemExit(1)
    finally:
        client.close()


def evidence_results_action() -> None:
    client, _gate = a100.connect()
    try:
        root, project = a100.paths(client)
        python = root + "/conda/envs/epidemic-core/bin/python"
        code = r'''import json,os,re
import sqlalchemy as sa
from sqlalchemy import text
engine=sa.create_engine(os.environ["DEMO_ADMIN_DATABASE_URL"],hide_parameters=True)
with engine.connect() as db:
 pub=dict(db.execute(text("SELECT p.id promotion_run_id,d.id approval_id,rc.id review_case_id,cv.id candidate_version_id,cv.candidate_id,c.discovery_run_id FROM epidemic_discovery.p1_9_publications x JOIN epidemic_discovery.source_promotion_runs p ON p.id=x.promotion_run_id JOIN epidemic_discovery.source_review_decisions d ON d.id=p.review_decision_id JOIN epidemic_discovery.source_review_cases rc ON rc.id=d.review_case_id JOIN epidemic_discovery.source_candidate_versions cv ON cv.id=rc.candidate_version_id JOIN epidemic_discovery.source_candidates c ON c.id=cv.candidate_id ORDER BY x.published_at DESC LIMIT 1")).mappings().one())
 candidate=dict(db.execute(text("""SELECT c.publisher,c.dataset_name,c.source_tier,c.status candidate_status,c.risk_flags,cv.publisher_evidence_sha256,cv.terms_sha256,cv.license_status,cv.format,cv.content_sha256,ar.sha256 artifact_sha256,ar.byte_size,ar.mime_type,ar.security_status,ar.security_report_sha256,ar.retrieved_at,sp.schema_sha256,sp.row_count,sp.column_count,sp.date_min,sp.date_max,qa.assessment_sha256,qa.status quality_status,qa.pii_status,qa.license_status assessed_license_status,mp.mapping_sha256,mp.status mapping_status
 FROM epidemic_discovery.source_candidates c JOIN epidemic_discovery.source_candidate_versions cv ON cv.candidate_id=c.id JOIN epidemic_discovery.quarantine_artifacts ar ON ar.candidate_version_id=cv.id JOIN epidemic_discovery.source_schema_profiles sp ON sp.candidate_version_id=cv.id JOIN epidemic_discovery.source_quality_assessments qa ON qa.candidate_version_id=cv.id JOIN epidemic_discovery.source_review_cases rc ON rc.candidate_version_id=cv.id JOIN epidemic_discovery.source_mapping_proposals mp ON mp.candidate_version_id=cv.id AND mp.revision=rc.current_mapping_revision WHERE cv.id=:version"""),{"version":pub["candidate_version_id"]}).mappings().one())
 searches=[dict(row) for row in db.execute(text("SELECT provider_id,domain,source_tier,media_type,disposition,result_sha256,observed_at FROM epidemic_discovery.source_discovery_search_results WHERE discovery_run_id=:run ORDER BY query_id,rank"),{"run":pub["discovery_run_id"]}).mappings()]
 fetches=[dict(row) for row in db.execute(text("SELECT status,http_status,content_type,byte_size,domain,elapsed_ms,artifact_sha256,failure_code,attempted_at,completed_at FROM epidemic_discovery.candidate_fetches WHERE discovery_run_id=:run ORDER BY attempted_at,id"),{"run":pub["discovery_run_id"]}).mappings()]
 events=[]
 for row in db.execute(text("SELECT event_code,details,created_at FROM epidemic_discovery.discovery_run_events WHERE discovery_run_id=:run ORDER BY created_at,id"),{"run":pub["discovery_run_id"]}).mappings():
  details=dict(row["details"] or {});safe={key:details.get(key) for key in ("provider_id","directory_id","result","reason_code","network_request","http_status","query_count","successful_query_count","result_count","elapsed_ms","domain") if key in details};events.append({"event_code":row["event_code"],"details":safe,"created_at":row["created_at"]})
 columns=[str(row[0]).lower() for row in db.execute(text("SELECT sc.source_name FROM epidemic_discovery.source_schema_columns sc JOIN epidemic_discovery.source_schema_profiles sp ON sp.id=sc.schema_profile_id WHERE sp.candidate_version_id=:version ORDER BY sc.ordinal"),{"version":pub["candidate_version_id"]})]
 preview_keys=set()
 for value in db.execute(text("SELECT sanitized_json FROM epidemic_discovery.source_preview_rows WHERE candidate_version_id=:version"),{"version":pub["candidate_version_id"]}).scalars():preview_keys.update((value or {}).keys())
def category(name):
 if name in {"dt_notific","sg_uf_not","id_municip","nu_ano"}:return "approved_preview_business_fields"
 if re.search(r"(?:cpf|nome|nm_|paciente|mae|telefone|email|endereco|logradouro|cartao|cns)",name):return "direct_or_quasi_identifier"
 if re.search(r"(?:sint|hospital|evol|obito|grav|gest|comorb|exame|resultado|vacina|trat|clin)",name):return "medical_sensitive"
 if re.search(r"(?:idade|sexo|raca|escolar|ocup|genero)",name):return "demographic"
 if re.search(r"(?:munic|bairro|cep|resid|\buf\b|_uf|geo)",name):return "geographic"
 if re.search(r"(?:^dt_|data|date|ano|semana|mes)",name):return "date_time"
 if re.search(r"(?:classi|criterio|caso|doenca|agravo)",name):return "case_classification"
 return "unclassified_or_public_business"
counts={}
for name in columns:counts[category(name)]=counts.get(category(name),0)+1
print(json.dumps({"lineage_ids":pub,"candidate":candidate,"search_results":searches,"fetches":fetches,"request_audit_events":events,"field_audit":{"column_count":len(columns),"category_counts":counts,"preview_key_count":len(preview_keys),"preview_keys_are_exact_whitelist":preview_keys<={"dt_notific","sg_uf_not","id_municip","nu_ano","case_notification_count"},"sensitive_names_emitted":False}},default=str))
'''
        setup = f"source {q(project + '/scripts/p1_9_demo/_common.sh')}; export PROJECT={q(project)}"
        result = remote_json(client, python, setup, code, timeout=300)
        print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
    finally:
        client.close()


def validate_code_action() -> None:
    client, _gate = a100.connect()
    try:
        root, project = a100.paths(client)
        python = root + "/conda/envs/epidemic-core/bin/python"
        commands = {
            "bash_syntax": "bash -n scripts/p1_9_demo/*.sh",
            "ruff": (
                f"{q(python)} -m ruff check migrations/versions/0016_p1_9_reviewed_promotion.py "
                "migrations/versions/0017_p1_9_crypto_resolution.py "
                "src/epidemic_core/p1_9 src/epidemic_core/discovery/promotion.py "
                "tests/unit/test_p1_9_promotion.py tests/unit/test_p1_9_ui.py"
            ),
            "format": (
                f"{q(python)} -m ruff format --check migrations/versions/0016_p1_9_reviewed_promotion.py "
                "migrations/versions/0017_p1_9_crypto_resolution.py "
                "src/epidemic_core/p1_9 src/epidemic_core/discovery/promotion.py "
                "tests/unit/test_p1_9_promotion.py tests/unit/test_p1_9_ui.py"
            ),
            "pytest": (
                f"PYTHONPATH=src {q(python)} -m pytest -q "
                "tests/unit/test_p1_9_promotion.py tests/unit/test_p1_9_ui.py"
            ),
        }
        results: dict[str, Any] = {}
        for name, command in commands.items():
            rc, output, error = a100.run(client, f"cd {q(project)} && {command}", timeout=600)
            results[name] = {"returncode": rc, "output": (output + error)[-4000:]}
        print(json.dumps(results, ensure_ascii=False, indent=2))
        if any(item["returncode"] != 0 for item in results.values()):
            raise SystemExit(1)
    finally:
        client.close()


def format_code_action() -> None:
    client, _gate = a100.connect()
    try:
        root, project = a100.paths(client)
        python = root + "/conda/envs/epidemic-core/bin/python"
        targets = (
            "migrations/versions/0016_p1_9_reviewed_promotion.py "
            "migrations/versions/0017_p1_9_crypto_resolution.py src/epidemic_core/p1_9 "
            "src/epidemic_core/discovery/promotion.py tests/unit/test_p1_9_promotion.py "
            "tests/unit/test_p1_9_ui.py"
        )
        a100.checked(client, f"cd {q(project)} && {q(python)} -m ruff check --fix {targets}", timeout=600)
        a100.checked(client, f"cd {q(project)} && {q(python)} -m ruff format {targets}", timeout=600)
        sftp = client.open_sftp()
        try:
            for relative in P1_9_FILES:
                if relative == "src/epidemic_core/discovery/review_app.py" or relative.startswith("scripts/"):
                    continue
                target = LOCAL_ROOT / "remote_edit" / "p1_9_work" / relative
                with sftp.file(project + "/" + relative, "rb") as remote:
                    target.write_bytes(remote.read())
        finally:
            sftp.close()
        print(json.dumps({"formatted": True}))
    finally:
        client.close()


def git_stage_inspect_action() -> None:
    client, _gate = a100.connect()
    try:
        _root, project = a100.paths(client)
        sftp = client.open_sftp()
        try:
            rows: list[dict[str, Any]] = []
            for relative in P1_9_FILES:
                rc, head_oid, _error = a100.run(
                    client,
                    f"git -C {q(project)} rev-parse {q('HEAD:' + relative)}",
                    timeout=120,
                )
                remote_path = project + "/" + relative
                try:
                    with sftp.file(remote_path, "rb") as remote:
                        work_bytes = remote.read()
                    work_exists = True
                except OSError:
                    work_bytes = b""
                    work_exists = False
                baseline_path = SNAPSHOT_ROOT / relative
                baseline_exists = baseline_path.is_file()
                baseline_bytes = baseline_path.read_bytes() if baseline_exists else b""
                def blob_oid(payload: bytes) -> str:
                    header = f"blob {len(payload)}\0".encode()
                    return hashlib.sha1(header + payload).hexdigest()

                clean_head_oid = head_oid.strip() if rc == 0 else ""
                head_sha256 = ""
                if rc == 0:
                    sha_rc, sha_output, _sha_error = a100.run(
                        client,
                        f"git -C {q(project)} show {q('HEAD:' + relative)} | sha256sum",
                        timeout=120,
                    )
                    if sha_rc == 0:
                        head_sha256 = sha_output.split()[0]
                rows.append(
                    {
                        "path": relative,
                        "head_exists": rc == 0,
                        "head_sha256": head_sha256,
                        "work_exists": work_exists,
                        "work_sha256": hashlib.sha256(work_bytes).hexdigest()
                        if work_exists
                        else "",
                        "baseline_exists": baseline_exists,
                        "baseline_sha256": hashlib.sha256(baseline_bytes).hexdigest()
                        if baseline_exists
                        else "",
                        "baseline_matches_head": baseline_exists
                        and rc == 0
                        and blob_oid(baseline_bytes) == clean_head_oid,
                        "baseline_matches_work": baseline_exists
                        and work_exists
                        and baseline_bytes == work_bytes,
                        "work_matches_head": work_exists
                        and rc == 0
                        and blob_oid(work_bytes) == clean_head_oid,
                    }
                )
        finally:
            sftp.close()
        status = a100.checked(
            client,
            f"git -C {q(project)} status --porcelain=v1 -uall -- "
            + " ".join(q(path) for path in P1_9_FILES),
        ).splitlines()
        print(
            json.dumps(
                {
                    "head": a100.checked(client, f"git -C {q(project)} rev-parse HEAD"),
                    "paths": rows,
                    "status_count": len(status),
                    "staged_count": len(
                        a100.checked(
                            client,
                            f"git -C {q(project)} diff --cached --name-only",
                        ).splitlines()
                    ),
                },
                indent=2,
                sort_keys=True,
            )
        )
    finally:
        client.close()


def git_diff_inspect_action() -> None:
    client, _gate = a100.connect()
    try:
        _root, project = a100.paths(client)
        paths = (
            "scripts/source_promotion_worker/_common.sh",
            "scripts/source_promotion_worker/start_source_promotion_worker.sh",
            "scripts/source_review/install_runtime.sh",
        )
        output = a100.checked(
            client,
            f"git -C {q(project)} diff --no-ext-diff --unified=8 HEAD -- "
            + " ".join(q(path) for path in paths),
        )
        print(output)
    finally:
        client.close()


def git_commit_action() -> None:
    expected_parent = "8ee7853860ad3bc69462062e3a1c3e6994d188b6"
    message = "feat(p1-9): add reviewed promotion and dashboard publication"
    client, _gate = a100.connect()
    try:
        _root, project = a100.paths(client)
        head_before = a100.checked(client, f"git -C {q(project)} rev-parse HEAD")
        if head_before != expected_parent:
            raise SystemExit("P1_9_COMMIT_PARENT_CHANGED")
        staged_at_entry = a100.checked(
            client, f"git -C {q(project)} diff --cached --name-only"
        ).splitlines()
        if staged_at_entry and sorted(staged_at_entry) != sorted(P1_9_FILES):
            raise SystemExit("P1_9_INDEX_CONTAINS_FOREIGN_PATHS")
        status_before = a100.checked(
            client, f"git -C {q(project)} status --porcelain=v1 -uall"
        ).splitlines()
        p1_9_status_before = a100.checked(
            client,
            f"git -C {q(project)} status --porcelain=v1 -uall -- "
            + " ".join(q(path) for path in P1_9_FILES),
        ).splitlines()
        if len(p1_9_status_before) != len(P1_9_FILES):
            raise SystemExit("P1_9_COMMIT_PATH_SET_INCOMPLETE")
        a100.checked(
            client,
            f"git -C {q(project)} add -- "
            + " ".join(q(path) for path in P1_9_FILES),
        )
        staged_paths = a100.checked(
            client, f"git -C {q(project)} diff --cached --name-only"
        ).splitlines()
        if sorted(staged_paths) != sorted(P1_9_FILES):
            raise SystemExit("P1_9_STAGED_SCOPE_INVALID")
        if a100.run(client, f"git -C {q(project)} diff --cached --check")[0] != 0:
            raise SystemExit("P1_9_STAGED_DIFF_CHECK_FAILED")
        scan_rules = {
            "private_key": re.compile(
                r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", re.I
            ),
            "cloud_token": re.compile(
                r"(?:AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9_]{30,}|sk-[A-Za-z0-9]{24,})"
            ),
            "credential_url": re.compile(
                r"(?:postgresql|https?)://[^\s:/]+:[^\s/@]+@", re.I
            ),
            "tls_bypass": re.compile(
                r"curl\s+-k(?:\s|$)|wget\s+--no-check-certificate|"
                r"GIT_SSL_NO_VERIFY\s*=\s*(?:1|true)|pip\s+[^\n]*--trusted-host",
                re.I,
            ),
            "proxy_literal": re.compile(
                r"(?:http|https|all)_proxy\s*=\s*['\"]?(?:https?|socks)://", re.I
            ),
        }
        finding_count = 0
        for relative in P1_9_FILES:
            content = a100.checked(
                client,
                f"git -C {q(project)} show {q(':' + relative)}",
                timeout=120,
            )
            finding_count += sum(
                len(tuple(pattern.finditer(content))) for pattern in scan_rules.values()
            )
        if finding_count:
            raise SystemExit("P1_9_STAGED_SECRET_OR_BYPASS_FINDING")
        commit_rc, _commit_output, _commit_error = a100.run(
            client,
            f"git -C {q(project)} commit -m {q(message)}",
            timeout=600,
        )
        if commit_rc != 0:
            raise SystemExit("P1_9_GIT_COMMIT_FAILED")
        head_after = a100.checked(client, f"git -C {q(project)} rev-parse HEAD")
        parent_after = a100.checked(client, f"git -C {q(project)} rev-parse HEAD^")
        subject_after = a100.checked(
            client, f"git -C {q(project)} show -s --format=%s HEAD"
        )
        committed_paths = a100.checked(
            client,
            f"git -C {q(project)} diff-tree --no-commit-id --name-only -r HEAD",
        ).splitlines()
        if (
            head_after == head_before
            or parent_after != expected_parent
            or subject_after != message
            or sorted(committed_paths) != sorted(P1_9_FILES)
        ):
            raise SystemExit("P1_9_COMMIT_VERIFICATION_FAILED")

        # Reapply the two pre-P1-9 A100 path customizations without embedding or
        # printing the server account path. P1-9's other changes stay in place.
        sftp = client.open_sftp()
        try:
            common_rel = "scripts/source_promotion_worker/_common.sh"
            common_base = (SNAPSHOT_ROOT / common_rel).read_bytes()
            common_project_line = next(
                line for line in common_base.splitlines(keepends=True) if line.startswith(b"PROJECT=")
            )
            common_path = project + "/" + common_rel
            with sftp.file(common_path, "rb") as remote:
                common_work = remote.read()
            common_dynamic = (
                b'SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)\n'
                b'PROJECT=$(cd -- "$SCRIPT_DIR/../.." && pwd -P)\n'
                b'ROOT=$(cd -- "$PROJECT/.." && pwd -P)\n'
                b'PYTHON="$ROOT/conda/envs/epidemic-core/bin/python"\n'
            )
            if common_work.count(common_dynamic) != 1:
                raise SystemExit("P1_9_COMMON_RESTORE_CONTEXT_INVALID")
            common_restored = common_work.replace(
                common_dynamic, common_project_line, 1
            )
            with sftp.file(common_path, "wb") as remote:
                remote.write(common_restored)
            sftp.chmod(common_path, 0o755)

            start_rel = "scripts/source_promotion_worker/start_source_promotion_worker.sh"
            start_base = (SNAPSHOT_ROOT / start_rel).read_bytes()
            start_source_line = next(
                line for line in start_base.splitlines(keepends=True) if line.startswith(b"source ")
            )
            start_path = project + "/" + start_rel
            with sftp.file(start_path, "rb") as remote:
                start_work = remote.read()
            start_dynamic = (
                b"#!/usr/bin/env bash\n"
                b"set -euo pipefail\n"
                b'SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)\n'
                b'source "$SCRIPT_DIR/_common.sh"\n'
            )
            if start_work.count(start_dynamic) != 1:
                raise SystemExit("P1_9_START_RESTORE_CONTEXT_INVALID")
            start_restored = start_work.replace(
                start_dynamic, b"#!/usr/bin/env bash\n" + start_source_line, 1
            )
            with sftp.file(start_path, "wb") as remote:
                remote.write(start_restored)
            sftp.chmod(start_path, 0o755)
        finally:
            sftp.close()

        status_after = a100.checked(
            client, f"git -C {q(project)} status --porcelain=v1 -uall"
        ).splitlines()
        p1_9_status_after = a100.checked(
            client,
            f"git -C {q(project)} status --porcelain=v1 -uall -- "
            + " ".join(q(path) for path in P1_9_FILES),
        ).splitlines()
        staged_after = a100.checked(
            client, f"git -C {q(project)} diff --cached --name-only"
        ).splitlines()
        if (
            len(status_after) != len(status_before) - (len(P1_9_FILES) - 2)
            or len(p1_9_status_after) != 2
            or staged_after
        ):
            raise SystemExit("P1_9_POST_COMMIT_WORKTREE_INVARIANT_FAILED")
        print(
            json.dumps(
                {
                    "commit": head_after,
                    "parent": parent_after,
                    "message": subject_after,
                    "committed_path_count": len(committed_paths),
                    "staged_path_count": 0,
                    "preexisting_dirty_before": len(status_before)
                    - (len(P1_9_FILES) - 2),
                    "preexisting_dirty_after": len(status_after),
                    "overlapping_preexisting_paths_restored": 2,
                },
                indent=2,
                sort_keys=True,
            )
        )
    finally:
        client.close()


def git_staged_check_action() -> None:
    client, _gate = a100.connect()
    try:
        _root, project = a100.paths(client)
        rc, output, error = a100.run(
            client, f"git -C {q(project)} diff --cached --check"
        )
        staged = a100.checked(
            client, f"git -C {q(project)} diff --cached --name-only"
        ).splitlines()
        print(
            json.dumps(
                {
                    "returncode": rc,
                    "diagnostics": (output + error).splitlines(),
                    "staged_path_count": len(staged),
                    "scope_matches": sorted(staged) == sorted(P1_9_FILES),
                },
                indent=2,
                sort_keys=True,
            )
        )
    finally:
        client.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action",
        choices=(
            "baseline", "snapshot", "upload", "format-code", "validate-code", "migration-probe",
            "upload-whitespace-fixes",
            "formal-migration", "setup-demo", "start-demo"
            , "configure-demo-auth", "copy-demo-username", "copy-demo-password"
            , "stage-demo-login"
            , "demo-diagnostics"
            , "apply-hotfix"
            , "hotfix-diagnostics"
            , "reset-demo"
            , "safe-fetcher-probe"
            , "restart-demo"
            , "relocate-misplaced-demo-artifact"
            , "e2e-results"
            , "integration-tests"
            , "deploy-formal-runtime"
            , "formal-runtime-diagnostics"
            , "stop-demo"
            , "ensure-demo"
            , "formal-immutability-check"
            , "cleanup-demo"
            , "full-validation"
            , "isolated-full-test"
             , "evidence-results"
            , "git-stage-inspect"
            , "git-diff-inspect"
            , "git-commit"
            , "git-staged-check"
        ),
    )
    args = parser.parse_args()
    if args.action == "baseline":
        baseline_action()
    elif args.action == "snapshot":
        snapshot_action()
    elif args.action == "upload":
        upload_action()
    elif args.action == "upload-whitespace-fixes":
        upload_whitespace_fixes_action()
    elif args.action == "validate-code":
        validate_code_action()
    elif args.action == "format-code":
        format_code_action()
    elif args.action == "formal-migration":
        formal_migration_action()
    elif args.action == "migration-probe":
        migration_probe_action()
    elif args.action == "setup-demo":
        setup_demo_action()
    elif args.action == "start-demo":
        start_demo_action()
    elif args.action == "configure-demo-auth":
        configure_demo_auth_action()
    elif args.action == "copy-demo-username":
        copy_demo_field_action("username")
    elif args.action == "copy-demo-password":
        copy_demo_field_action("password")
    elif args.action == "stage-demo-login":
        stage_demo_login_action()
    elif args.action == "demo-diagnostics":
        demo_diagnostics_action()
    elif args.action == "apply-hotfix":
        apply_hotfix_action()
    elif args.action == "hotfix-diagnostics":
        hotfix_diagnostics_action()
    elif args.action == "reset-demo":
        reset_demo_action()
    elif args.action == "safe-fetcher-probe":
        safe_fetcher_probe_action()
    elif args.action == "restart-demo":
        restart_demo_action()
    elif args.action == "relocate-misplaced-demo-artifact":
        relocate_misplaced_demo_artifact_action()
    elif args.action == "e2e-results":
        e2e_results_action()
    elif args.action == "integration-tests":
        integration_tests_action()
    elif args.action == "deploy-formal-runtime":
        deploy_formal_runtime_action()
    elif args.action == "formal-runtime-diagnostics":
        formal_runtime_diagnostics_action()
    elif args.action == "stop-demo":
        stop_demo_action()
    elif args.action == "ensure-demo":
        ensure_demo_action()
    elif args.action == "formal-immutability-check":
        formal_immutability_check_action()
    elif args.action == "cleanup-demo":
        cleanup_demo_action()
    elif args.action == "full-validation":
        full_validation_action()
    elif args.action == "isolated-full-test":
        isolated_full_test_action()
    elif args.action == "evidence-results":
        evidence_results_action()
    elif args.action == "git-stage-inspect":
        git_stage_inspect_action()
    elif args.action == "git-diff-inspect":
        git_diff_inspect_action()
    elif args.action == "git-commit":
        git_commit_action()
    elif args.action == "git-staged-check":
        git_staged_check_action()


if __name__ == "__main__":
    main()
