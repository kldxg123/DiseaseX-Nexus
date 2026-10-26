from __future__ import annotations

import argparse
import base64
import hashlib
import json
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import a100_p1_10r3 as p110r3


ROOT = Path(__file__).resolve().parents[1]
HEAD_ROOT = ROOT / "remote_edit" / "p1_11_head"
CURRENT_ROOT = ROOT / "remote_edit" / "p1_11_current"
WORK_ROOT = ROOT / "remote_edit" / "p1_11_work"
GATE_ROOT = ROOT / "remote_edit" / "p1_11_gate"
REPORT_ROOT = ROOT / "reports" / "p1_11_ebola_daily_monitor_and_demo_chains"
EXPECTED_HEAD = "d0848bf1cf069b8d9ac0bc6ff4bd1e5e7f5b65e8"
MONITORING_COMMIT_SUBJECT = "feat(p1-11): add official Ebola daily monitoring"
PREVIEW_COMMIT_SUBJECT = "feat(p1-11): add three-source Ebola preview demos"
PRESERVATION_COMMIT_SUBJECT = "fix(p1-11): preserve pre-existing dashboard runtime change"
REMOTE_PROBE = ROOT / "tools" / "remote_p1_11_live_probe.py"
CHANGE_FILES = (
    "pyproject.toml",
    "migrations/versions/0021_p1_11_ebola_monitoring.py",
    "reference/active_catalogs.v3.json",
    "reference/diseases.v2.json",
    "reference/metrics.v3.json",
    "src/epidemic_core/reference.py",
    "src/epidemic_core/daily/main.py",
    "src/epidemic_core/daily/worker.py",
    "src/epidemic_core/dashboard/app.py",
    "src/epidemic_core/dashboard/static/dashboard.p1-11-ebola.css",
    "src/epidemic_core/discovery/review_app.py",
    "src/epidemic_core/p1_10/diagnostics.py",
    "src/epidemic_core/p1_10/preview.py",
    "src/epidemic_core/ebola/__init__.py",
    "src/epidemic_core/ebola/cli.py",
    "src/epidemic_core/ebola/dashboard_app.py",
    "src/epidemic_core/ebola/fetcher.py",
    "src/epidemic_core/ebola/parsers.py",
    "src/epidemic_core/ebola/preview.py",
    "src/epidemic_core/ebola/review_app.py",
    "src/epidemic_core/ebola/service.py",
    "scripts/source_review/install_runtime.sh",
    "scripts/dashboard/install_runtime.sh",
    "tests/test_p1_11_ebola_parsers.py",
    "tests/integration/test_postgres.py",
)


def q(value: str) -> str:
    return p110r3.q(value)


def status_path(line: str) -> str:
    return p110r3.previous._status_path(line)


def capture_action() -> None:
    client, gate = p110r3.previous.p19.a100.connect()
    try:
        data_root, project = p110r3.previous.p19.a100.paths(client)
        head = p110r3.previous.p19.a100.checked(client, f"git -C {q(project)} rev-parse HEAD")
        if head != EXPECTED_HEAD:
            raise SystemExit("P1_11_BASELINE_HEAD_CHANGED")
        branch = p110r3.previous.p19.a100.checked(client, f"git -C {q(project)} branch --show-current")
        staged = p110r3.previous.p19.a100.checked(client, f"git -C {q(project)} diff --cached --name-only")
        if staged:
            raise SystemExit("P1_11_BASELINE_INDEX_NOT_EMPTY")
        status = p110r3.previous.p19.a100.checked(client, f"git -C {q(project)} status --porcelain=v2 -uall")
        dirty_paths = sorted({status_path(line) for line in status.splitlines() if line})
        tracked = p110r3.previous.p19.a100.checked(client, f"git -C {q(project)} ls-files").splitlines()
        prefixes = (
            "src/epidemic_core/",
            "migrations/versions/",
            "tests/",
            "scripts/daily_update/",
            "scripts/source_discovery_worker/",
            "scripts/preview_publication_worker/",
            "scripts/dashboard/",
            "scripts/source_review/",
            "scripts/database/",
            "openclaw-plugin/",
            "reference/",
            "config/",
            "contracts/",
        )
        selected = [path for path in tracked if path.startswith(prefixes)]
        selected.extend(path for path in tracked if path in {"pyproject.toml", "alembic.ini", "package.json", "package-lock.json"})
        selected.extend(path for path in dirty_paths if path not in selected and path.startswith(prefixes))
        selected = sorted(set(selected))
        for directory in (HEAD_ROOT, CURRENT_ROOT, WORK_ROOT):
            if directory.exists():
                shutil.rmtree(directory)
            directory.mkdir(parents=True)
        sftp = client.open_sftp()
        try:
            for relative in selected:
                current_target = CURRENT_ROOT / relative
                current_target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    sftp.get(f"{project}/{relative}", str(current_target))
                except OSError:
                    continue
                head_target = HEAD_ROOT / relative
                head_target.parent.mkdir(parents=True, exist_ok=True)
                rc, output, _error = p110r3.previous.p19.a100.run(
                    client,
                    f"git -C {q(project)} show {q('HEAD:' + relative)} | base64 -w0",
                    timeout=120,
                )
                if rc == 0:
                    head_target.write_bytes(base64.b64decode(output))
        finally:
            sftp.close()
        shutil.copytree(CURRENT_ROOT, WORK_ROOT, dirs_exist_ok=True)
        GATE_ROOT.mkdir(parents=True, exist_ok=True)
        result = {
            "captured_at_utc": datetime.now(timezone.utc).isoformat(),
            "branch": branch,
            "head": head,
            "dirty_count": len(dirty_paths),
            "dirty_paths": dirty_paths,
            "staged_count": 0,
            "selected_count": len(selected),
            "downloaded_count": len(list(CURRENT_ROOT.rglob("*"))) - len(list(CURRENT_ROOT.rglob("*/"))),
            "target_identity_verified": bool(gate.get("stable_second_connection")),
            "secrets_exposed": False,
        }
        (GATE_ROOT / "baseline.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
        print(
            json.dumps(
                {key: result[key] for key in ("branch", "head", "dirty_count", "staged_count", "selected_count", "target_identity_verified")},
                ensure_ascii=True,
                indent=2,
            )
        )
    finally:
        client.close()


def live_probe_action() -> None:
    client, gate = p110r3.previous.p19.a100.connect()
    try:
        data_root, project = p110r3.previous.p19.a100.paths(client)
        head = p110r3.previous.p19.a100.checked(client, f"git -C {q(project)} rev-parse HEAD")
        if head != EXPECTED_HEAD:
            raise SystemExit("P1_11_BASELINE_HEAD_CHANGED")
        if not gate.get("stable_second_connection"):
            raise SystemExit("P1_11_TARGET_IDENTITY_UNVERIFIED")
        remote_root = f"{project}/storage/p1_11/live_probe"
        remote_script = f"{remote_root}/remote_p1_11_live_probe.py"
        p110r3.previous.p19.a100.checked(client, f"mkdir -p {q(remote_root)}")
        sftp = client.open_sftp()
        try:
            sftp.put(str(REMOTE_PROBE), remote_script)
        finally:
            sftp.close()
        command = (
            "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; "
            f"{q(data_root + '/conda/envs/epidemic-core/bin/python')} {q(remote_script)} {q(remote_root)}"
        )
        rc, output, error = p110r3.previous.p19.a100.run(client, command, timeout=600)
        if rc not in (0, 2):
            raise SystemExit(f"P1_11_LIVE_PROBE_FAILED:{error.strip()[:120]}")
        result = json.loads(output.strip().splitlines()[-1])
        GATE_ROOT.mkdir(parents=True, exist_ok=True)
        (GATE_ROOT / "live_probe_results.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    "fetch_count": len(result.get("fetches", [])),
                    "domains": result.get("allowlisted_domains", []),
                    "failure_count": len(result.get("failures", [])),
                    "insp_article_count": result.get("insp_article_count"),
                    "insp_asset_count": result.get("insp_asset_count"),
                    "ocr": result.get("ocr", []),
                    "proxy_present_during_probe": result.get("proxy_present_during_probe"),
                    "tls_verification": result.get("tls_verification"),
                },
                ensure_ascii=True,
                indent=2,
            )
        )
    finally:
        client.close()


def download_probe_action() -> None:
    client, _gate = p110r3.previous.p19.a100.connect()
    try:
        _data_root, project = p110r3.previous.p19.a100.paths(client)
        manifest = json.loads(
            p110r3.previous.p19.a100.checked(
                client, f"python3 -c {q('import json;print(json.dumps(json.load(open(' + repr(project + '/storage/p1_11/live_probe/live_probe_manifest.json') + '))))')}"
            )
        )
        target_root = ROOT / "remote_edit" / "p1_11_probe"
        if target_root.exists():
            shutil.rmtree(target_root)
        target_root.mkdir(parents=True)
        sftp = client.open_sftp()
        try:
            for row in manifest.get("fetches", []):
                storage_key = str(row["storage_key"])
                target = target_root / storage_key
                target.parent.mkdir(parents=True, exist_ok=True)
                sftp.get(f"{project}/storage/p1_11/live_probe/{storage_key}", str(target))
        finally:
            sftp.close()
        (target_root / "live_probe_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
        print(json.dumps({"downloaded": len(manifest.get("fetches", [])), "secrets_exposed": False}))
    finally:
        client.close()


def upload_stage_action() -> None:
    client, gate = p110r3.previous.p19.a100.connect()
    try:
        data_root, project = p110r3.previous.p19.a100.paths(client)
        if p110r3.previous.p19.a100.checked(client, f"git -C {q(project)} rev-parse HEAD") != EXPECTED_HEAD:
            raise SystemExit("P1_11_BASELINE_HEAD_CHANGED")
        stage = f"{project}/storage/p1_11/stage_{int(time.time())}"
        p110r3.previous.p19.a100.checked(
            client,
            f"mkdir -p {q(stage)} && git -C {q(project)} archive HEAD | tar -x -C {q(stage)}",
            timeout=300,
        )
        sftp = client.open_sftp()
        try:
            for relative in CHANGE_FILES:
                source = WORK_ROOT / relative
                if not source.is_file():
                    raise SystemExit(f"P1_11_LOCAL_FILE_MISSING:{relative}")
                remote = f"{stage}/{relative}"
                parent = remote.rsplit("/", 1)[0]
                p110r3.previous.p19.a100.checked(client, f"mkdir -p {q(parent)}")
                sftp.put(str(source), remote)
        finally:
            sftp.close()
        python = f"{data_root}/conda/envs/epidemic-core/bin/python"
        install = (
            "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; "
            f"{q(python)} -m pip install --disable-pip-version-check --no-input --timeout 60 --retries 2 "
            f"--cache-dir {q(project + '/storage/cache/pip')} pypdf==6.14.2"
        )
        p110r3.previous.p19.a100.checked(client, install, timeout=600)
        state = {
            "stage": stage,
            "head": EXPECTED_HEAD,
            "file_count": len(CHANGE_FILES),
            "target_identity_verified": bool(gate.get("stable_second_connection")),
            "production_worktree_modified": False,
        }
        GATE_ROOT.mkdir(parents=True, exist_ok=True)
        (GATE_ROOT / "stage.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
        print(json.dumps(state, indent=2))
    finally:
        client.close()


def stage_validate_action() -> None:
    state = json.loads((GATE_ROOT / "stage.json").read_text(encoding="utf-8"))
    stage = str(state["stage"])
    client, _gate = p110r3.previous.p19.a100.connect()
    try:
        data_root, project = p110r3.previous.p19.a100.paths(client)
        if not stage.startswith(project + "/storage/p1_11/stage_"):
            raise SystemExit("P1_11_UNSAFE_STAGE")
        python = f"{data_root}/conda/envs/epidemic-core/bin/python"
        python_files = [path for path in CHANGE_FILES if path.endswith(".py")]
        files = " ".join(q(path) for path in python_files)
        command = (
            f"cd {q(stage)} && {q(python)} -m ruff check --fix {files} && {q(python)} -m ruff format {files} "
            f"&& {q(python)} -m ruff check {files} && {q(python)} -m ruff format --check {files} "
            f"&& PYTHONPATH={q(stage + '/src')} {q(python)} -m pytest -q tests/test_p1_11_ebola_parsers.py "
            f"&& PYTHONPATH={q(stage + '/src')} {q(python)} -m mypy --strict src/epidemic_core/ebola"
        )
        rc, output, error = p110r3.previous.p19.a100.run(client, f"bash -lc {q(command)}", timeout=900)
        if rc:
            safe_tail = (output + "\n" + error).splitlines()[-80:]
            raise SystemExit("P1_11_STAGE_VALIDATION_FAILED\n" + "\n".join(safe_tail))
        sftp = client.open_sftp()
        try:
            for relative in python_files:
                target = WORK_ROOT / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                sftp.get(f"{stage}/{relative}", str(target))
        finally:
            sftp.close()
        result = {"pass": True, "formatted_files": len(python_files), "output_tail": output.splitlines()[-20:]}
        (GATE_ROOT / "stage_validation.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result, indent=2))
    finally:
        client.close()


def isolated_e2e_action() -> None:
    state = json.loads((GATE_ROOT / "stage.json").read_text(encoding="utf-8"))
    stage = str(state["stage"])
    client, _gate = p110r3.previous.p19.a100.connect()
    try:
        data_root, project = p110r3.previous.p19.a100.paths(client)
        if not stage.startswith(project + "/storage/p1_11/stage_"):
            raise SystemExit("P1_11_UNSAFE_STAGE")
        python = f"{data_root}/conda/envs/epidemic-core/bin/python"
        code = r'''
import hashlib,json,os,pathlib,re,secrets,subprocess,sys,uuid
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import make_url
project=pathlib.Path(os.environ["PROJECT"]);stage=pathlib.Path(os.environ["STAGE"]);python=os.environ["PYTHON_BIN"];pgbin=pathlib.Path(os.environ["POSTGRES_BIN"])
formal=make_url(os.environ["MIGRATION_DATABASE_URL"]).set(username="epidemicdb",password=os.environ["POSTGRES_ADMIN_PASSWORD"]);app_url=make_url(os.environ["APP_DATABASE_URL"]);cluster=formal.set(database="postgres")
name="epidemic_p1_11_test_"+secrets.token_hex(5);dump=stage/"storage/isolated-e2e/formal.dump";dump.parent.mkdir(parents=True,exist_ok=True)
env=os.environ.copy();env["PGPASSWORD"]=formal.password or "";checks=[]
def pgargs(url):return ["-h",url.host or "127.0.0.1","-p",str(url.port or 5432),"-U",url.username or "epidemicdb","-d",url.database]
def run(label,args,timeout=1800,extra=None):
 e=env.copy();e.update(extra or {});p=subprocess.run(args,cwd=stage,env=e,text=True,capture_output=True,timeout=timeout)
 safe=re.sub(r"postgresql(?:\+\w+)?://[^\s'\"]+","[DATABASE_URL_REDACTED]",p.stdout+"\n"+p.stderr).replace(str(project),"[project]").replace(str(stage),"[stage]")
 checks.append({"label":label,"returncode":p.returncode,"output_sha256":hashlib.sha256(safe.encode()).hexdigest(),"tail":safe.splitlines()[-12:]})
 if p.returncode:raise RuntimeError(label+" failed sha="+hashlib.sha256(safe.encode()).hexdigest()+" tail="+" | ".join(safe.splitlines()[-5:]))
run("formal_dump",[str(pgbin/"pg_dump"),*pgargs(formal),"--format=custom","--file",str(dump)])
cluster_engine=sa.create_engine(cluster,isolation_level="AUTOCOMMIT",hide_parameters=True)
with cluster_engine.connect() as db:db.exec_driver_sql('CREATE DATABASE "'+name+'" TEMPLATE template0 ENCODING \'UTF8\'')
trusted=("data_sources","query_requests","retrieval_runs","source_fetches","raw_artifacts","normalized_observations","quality_assessments","dataset_snapshots","dataset_snapshot_observations")
def inventory(engine):
 with engine.connect() as db:return {"counts":{n:int(db.execute(text("SELECT count(*) FROM epidemic."+n)).scalar_one()) for n in trusted},"snapshots":[dict(r) for r in db.execute(text("SELECT id,row_count,content_sha256 FROM epidemic.dataset_snapshots ORDER BY created_at,id")).mappings()]}
source_engine=sa.create_engine(formal,hide_parameters=True);source_inventory=inventory(source_engine)
try:
 clone=formal.set(database=name);run("restore",[str(pgbin/"pg_restore"),*pgargs(clone),"--exit-on-error","--no-owner",str(dump)])
 clone_engine=sa.create_engine(clone,hide_parameters=True);restored=inventory(clone_engine)
 if restored!=source_inventory:raise RuntimeError("RESTORED_TRUSTED_INVENTORY_MISMATCH")
 app_env={"MIGRATION_DATABASE_URL":clone.render_as_string(hide_password=False),"APP_DATABASE_URL":app_url.set(database=name).render_as_string(hide_password=False),"PYTHONPATH":str(stage/"src"),"PYTHONNOUSERSITE":"1"}
 run("upgrade_0021",[python,"-m","alembic","upgrade","head"],900,app_env)
 run("seed_reference_v3",[python,"-m","epidemic_core.cli","seed-reference"],600,app_env)
 run("initial_live_backfill",[python,"-m","epidemic_core.ebola.cli","initial-backfill"],1800,app_env)
 with clone_engine.connect() as db:
  revision=db.execute(text("SELECT version_num FROM epidemic.alembic_version")).scalar_one()
  counts={table:int(db.execute(text("SELECT count(*) FROM epidemic_ebola."+table)).scalar_one()) for table in ("source_contracts","monitor_schedules","source_report_versions","source_report_observations","candidate_bindings","preview_decisions","preview_jobs","preview_publications")}
  source_counts=[dict(r) for r in db.execute(text("SELECT c.source_key,count(DISTINCT v.id) report_versions,count(o.id) observations,min(o.source_as_of) date_min,max(o.source_as_of) date_max FROM epidemic_ebola.source_contracts c JOIN epidemic_ebola.source_report_versions v ON v.source_id=c.id JOIN epidemic_ebola.source_report_observations o ON o.report_version_id=v.id GROUP BY c.source_key ORDER BY c.source_key")).mappings()]
  who_points=int(db.execute(text("SELECT count(DISTINCT o.source_as_of) FROM epidemic_ebola.source_report_observations o JOIN epidemic_ebola.source_report_versions v ON v.id=o.report_version_id JOIN epidemic_ebola.source_contracts c ON c.id=v.source_id WHERE c.source_key='who-don-bvd-2026' AND o.country_iso3='COD' AND o.metric_code='ebola_confirmed_cases_cumulative'")).scalar_one())
  uganda_semantics=[dict(r) for r in db.execute(text("SELECT import_classification,local_transmission,value_numeric FROM epidemic_ebola.source_report_observations o JOIN epidemic_ebola.source_report_versions v ON v.id=o.report_version_id JOIN epidemic_ebola.source_contracts c ON c.id=v.source_id WHERE c.source_key='uganda-moh-ebola-daily' AND o.metric_code='ebola_confirmed_cases_cumulative' ORDER BY import_classification")).mappings()]
  candidates=[dict(r) for r in db.execute(text("SELECT review_case_id,state_version,source_key,artifact_sha256,mapping_sha256 FROM epidemic_ebola.review_candidates_v1 ORDER BY source_key")).mappings()]
  metrics=int(db.execute(text("SELECT count(*) FROM epidemic.metric_definitions WHERE metric_code LIKE 'ebola_%'")).scalar_one());disease=int(db.execute(text("SELECT count(*) FROM epidemic.diseases WHERE slug='ebola-disease'")).scalar_one())
 if revision!="0021_p1_11_ebola_monitoring" or counts!={"source_contracts":4,"monitor_schedules":3,"source_report_versions":13,"source_report_observations":55,"candidate_bindings":3,"preview_decisions":0,"preview_jobs":0,"preview_publications":0}:raise RuntimeError("P1_11_BACKFILL_COUNTS_INVALID "+str(counts))
 if who_points<5 or len(candidates)!=3 or metrics!=15 or disease!=1:raise RuntimeError("P1_11_SEMANTIC_COUNTS_INVALID")
 run("idempotent_live_backfill",[python,"-m","epidemic_core.ebola.cli","initial-backfill"],1800,app_env)
 with clone_engine.connect() as db:
  counts_after={table:int(db.execute(text("SELECT count(*) FROM epidemic_ebola."+table)).scalar_one()) for table in counts}
 if counts_after!=counts:raise RuntimeError("P1_11_IDEMPOTENCY_FAILED "+str(counts_after))
 confirmations=json.dumps({"not_trusted":True,"license_reviewed":True,"aggregate_only":True,"no_cross_source_sum":True,"no_training":True,"no_prediction":True})
 with clone_engine.begin() as db:
  for row in candidates:
   db.execute(text("SELECT epidemic_ebola.submit_preview_decision(:case,'isolated-human-review','isolated database review only',:key,:version,CAST(:confirmations AS jsonb),'p1_11_test',true)"),{"case":row["review_case_id"],"key":uuid.uuid4(),"version":row["state_version"],"confirmations":confirmations}).scalar_one()
 sys.path.insert(0,str(stage/"src"))
 from epidemic_core.ebola.preview import process_one
 for index in range(3):
  if not process_one(clone_engine,"p1-11-isolated-worker"):raise RuntimeError("P1_11_PREVIEW_JOB_NOT_CLAIMED")
 with clone_engine.connect() as db:
  e2e={"decisions":int(db.execute(text("SELECT count(*) FROM epidemic_ebola.preview_decisions")).scalar_one()),"published":int(db.execute(text("SELECT count(*) FROM epidemic_ebola.preview_publications")).scalar_one()),"observations":int(db.execute(text("SELECT count(*) FROM epidemic_ebola.preview_observations")).scalar_one()),"sources":int(db.execute(text("SELECT count(DISTINCT source_key) FROM epidemic_ebola.preview_publications")).scalar_one()),"cross_source_sum_true":int(db.execute(text("SELECT count(*) FROM epidemic_ebola.preview_publications WHERE cross_source_sum_allowed")).scalar_one())}
  def hp(role,kind,target,priv):
   fn={"schema":"has_schema_privilege","table":"has_table_privilege","function":"has_function_privilege"}[kind]
   return bool(db.execute(text("SELECT "+fn+"(:role,:target,:priv)"),{"role":role,"target":target,"priv":priv}).scalar_one())
  permissions={
   "review_view":hp("epidemicreview","table","epidemic_ebola.review_candidates_v1","SELECT"),
   "review_submit":hp("epidemicreview","function","epidemic_ebola.submit_preview_decision(uuid,text,text,uuid,integer,jsonb,text,boolean)","EXECUTE"),
   "review_write_denied":not hp("epidemicreview","table","epidemic_ebola.preview_observations","INSERT"),
   "preview_write":hp("epidemicpreview","table","epidemic_ebola.preview_observations","INSERT"),
   "preview_trusted_denied":not hp("epidemicpreview","table","epidemic.normalized_observations","INSERT"),
   "dashboard_view":hp("epidemic_dashboard_ro","table","epidemic_ebola.preview_series_v1","SELECT"),
   "dashboard_write_denied":not hp("epidemic_dashboard_ro","table","epidemic_ebola.preview_observations","INSERT"),
   "app_preview_write_denied":not hp("epidemic_app","table","epidemic_ebola.preview_observations","INSERT"),
   "public_schema_denied":not hp("public","schema","epidemic_ebola","USAGE"),
  }
  permission_denials={
   "review_preview_insert":not hp("epidemicreview","table","epidemic_ebola.preview_observations","INSERT"),
   "review_report_insert":not hp("epidemicreview","table","epidemic_ebola.source_report_observations","INSERT"),
   "review_job_update":not hp("epidemicreview","table","epidemic_ebola.preview_jobs","UPDATE"),
   "preview_decision_insert":not hp("epidemicpreview","table","epidemic_ebola.preview_decisions","INSERT"),
   "preview_report_update":not hp("epidemicpreview","table","epidemic_ebola.source_report_versions","UPDATE"),
   "preview_trusted_insert":not hp("epidemicpreview","table","epidemic.normalized_observations","INSERT"),
   "dashboard_preview_insert":not hp("epidemic_dashboard_ro","table","epidemic_ebola.preview_observations","INSERT"),
   "dashboard_preview_update":not hp("epidemic_dashboard_ro","table","epidemic_ebola.preview_observations","UPDATE"),
   "dashboard_trusted_insert":not hp("epidemic_dashboard_ro","table","epidemic.normalized_observations","INSERT"),
   "app_preview_insert":not hp("epidemic_app","table","epidemic_ebola.preview_observations","INSERT"),
   "public_schema_usage":not hp("public","schema","epidemic_ebola","USAGE"),
   "public_review_view":not hp("public","table","epidemic_ebola.review_candidates_v1","SELECT"),
  }
 if e2e!={"decisions":3,"published":3,"observations":55,"sources":3,"cross_source_sum_true":0} or not all(permissions.values()) or not all(permission_denials.values()):raise RuntimeError("P1_11_E2E_OR_PERMISSION_FAILED "+str(e2e)+str(permissions)+str(permission_denials))
 final_inventory=inventory(clone_engine)
 if final_inventory!=source_inventory:raise RuntimeError("P1_11_TRUSTED_INVENTORY_CHANGED")
 run("downgrade_0020",[python,"-m","alembic","downgrade","0020_p1_10r3_preview_permissions"],900,app_env)
 with clone_engine.connect() as db:down=db.execute(text("SELECT version_num FROM epidemic.alembic_version")).scalar_one();absent=bool(db.execute(text("SELECT to_regnamespace('epidemic_ebola') IS NULL")).scalar_one())
 run("reupgrade_0021",[python,"-m","alembic","upgrade","head"],900,app_env);run("single_head",[python,"-m","alembic","heads"],300,app_env)
 with clone_engine.connect() as db:up=db.execute(text("SELECT version_num FROM epidemic.alembic_version")).scalar_one()
 clone_engine.dispose()
 result={"pass":True,"revision":revision,"counts":counts,"source_counts":source_counts,"who_cod_points":who_points,"uganda_semantics":uganda_semantics,"catalog":{"ebola_metrics":metrics,"ebola_disease":disease},"idempotent":True,"isolated_e2e":e2e,"permissions":permissions,"permission_denials":permission_denials,"trusted_inventory_unchanged":True,"downgrade_revision":down,"schema_absent_after_downgrade":absent,"reupgrade_revision":up,"checks":checks,"formal_database_modified":False,"secrets_exposed":False}
 (dump.parent/"result.json").write_text(json.dumps(result,default=str,ensure_ascii=False),encoding="utf-8")
 print(json.dumps(result,default=str,ensure_ascii=False))
except Exception as exc:
 failure={"pass":False,"error_type":type(exc).__name__,"error":re.sub(r"postgresql(?:\+\w+)?://[^\s'\"]+","[DATABASE_URL_REDACTED]",str(exc)).replace(str(project),"[project]").replace(str(stage),"[stage]")}
 (dump.parent/"failure.json").write_text(json.dumps(failure,ensure_ascii=False),encoding="utf-8")
 raise
finally:
 with cluster_engine.connect() as db:
  db.execute(text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=:name"),{"name":name});db.exec_driver_sql('DROP DATABASE IF EXISTS "'+name+'"')
 cluster_engine.dispose();source_engine.dispose()
'''
        setup = (
            f"source {q(project + '/scripts/database/_env.sh')}; set -a; "
            f"source {q(project + '/.secrets/postgres.env')}; set +a; "
            "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; "
            f"export PROJECT={q(project)} STAGE={q(stage)} PYTHON_BIN={q(python)} "
            f"POSTGRES_BIN={q(data_root + '/conda/envs/postgresql18/bin')}"
        )
        result = p110r3.previous._remote_json(client, python, setup, code, timeout=7200)
        (GATE_ROOT / "isolated_e2e.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, default=str), encoding="utf-8"
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    finally:
        client.close()


def status_action() -> None:
    """Report only sanitized process state for the disposable P1-11 gate."""
    client, _gate = p110r3.previous.p19.a100.connect()
    try:
        data_root, project = p110r3.previous.p19.a100.paths(client)
        stage = str(json.loads((GATE_ROOT / "stage.json").read_text(encoding="utf-8"))["stage"])
        command = (
            "ps -eo pid=,ppid=,nlwp=,etime=,stat=,pcpu=,pmem=,comm= | "
            "awk '$4 ~ /^[0-9][0-9]:/ && $8 == \"python\" {print}' | head -n 30"
        )
        rc, output, error = p110r3.previous.p19.a100.run(client, command, timeout=60)
        db_command = (
            f"source {q(project + '/scripts/database/_env.sh')}; set -a; "
            f"source {q(project + '/.secrets/postgres.env')}; set +a; "
            "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; "
            f"PGPASSWORD=\"$POSTGRES_ADMIN_PASSWORD\" {q(data_root + '/conda/envs/postgresql18/bin/psql')} "
            "-h 127.0.0.1 -p 5432 -U epidemicdb -d postgres -AtX "
            "-c \"SELECT count(*) FROM pg_database WHERE datname LIKE 'epidemic_p1_11_test_%'\""
        )
        db_rc, db_output, db_error = p110r3.previous.p19.a100.run(
            client, f"bash -lc {q(db_command)}", timeout=60
        )
        completed_result: dict[str, object] | None = None
        latest_completed_result: dict[str, object] | None = None
        latest_completed_mtime = 0
        sftp = client.open_sftp()
        try:
            for name in ("result.json", "failure.json"):
                try:
                    with sftp.open(f"{stage}/storage/isolated-e2e/{name}", "r") as handle:
                        completed_result = json.loads(handle.read().decode("utf-8"))
                    break
                except FileNotFoundError:
                    continue
            for entry in sftp.listdir_attr(f"{project}/storage/p1_11"):
                if not entry.filename.startswith("stage_"):
                    continue
                for name in ("result.json", "failure.json"):
                    candidate = f"{project}/storage/p1_11/{entry.filename}/storage/isolated-e2e/{name}"
                    try:
                        attrs = sftp.stat(candidate)
                        if int(attrs.st_mtime) <= latest_completed_mtime:
                            continue
                        with sftp.open(candidate, "r") as handle:
                            latest_completed_result = json.loads(handle.read().decode("utf-8"))
                        latest_completed_mtime = int(attrs.st_mtime)
                    except FileNotFoundError:
                        continue
        finally:
            sftp.close()
        if latest_completed_result is not None:
            (GATE_ROOT / "isolated_e2e.json").write_text(
                json.dumps(latest_completed_result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
            )
        print(
            json.dumps(
                {
                    "returncode": rc,
                    "processes": output.splitlines(),
                    "stderr_present": bool(error),
                    "test_database_count": int(db_output.strip()) if db_rc == 0 else None,
                    "database_probe_stderr_present": bool(db_error),
                    "completed_result": completed_result,
                    "latest_completed_result": latest_completed_result,
                },
                indent=2,
            )
        )
    finally:
        client.close()


def deploy_action() -> None:
    baseline = json.loads((GATE_ROOT / "baseline.json").read_text(encoding="utf-8"))
    if set(baseline["dirty_paths"]) & set(CHANGE_FILES):
        raise SystemExit("P1_11_DEPLOY_CHANGE_OVERLAPS_BASELINE_DIRTY")
    client, _gate = p110r3.previous.p19.a100.connect()
    try:
        data_root, project = p110r3.previous.p19.a100.paths(client)
        if p110r3.previous.p19.a100.checked(client, f"git -C {q(project)} rev-parse HEAD") != EXPECTED_HEAD:
            raise SystemExit("P1_11_DEPLOY_HEAD_CHANGED")
        if p110r3.previous.p19.a100.checked(client, f"git -C {q(project)} diff --cached --name-only"):
            raise SystemExit("P1_11_DEPLOY_INDEX_NOT_EMPTY")
        sftp = client.open_sftp()
        temporary: list[tuple[str, str, int]] = []
        uploaded: list[str] = []
        try:
            for relative in CHANGE_FILES:
                source = WORK_ROOT / relative
                if not source.is_file():
                    raise SystemExit(f"P1_11_DEPLOY_SOURCE_MISSING:{relative}")
                remote = f"{project}/{relative}"
                desired = source.read_bytes()
                desired_sha = hashlib.sha256(desired).hexdigest()
                baseline_file = CURRENT_ROOT / relative
                baseline_sha = hashlib.sha256(baseline_file.read_bytes()).hexdigest() if baseline_file.is_file() else None
                try:
                    with sftp.open(remote, "rb") as handle:
                        current = handle.read()
                    exists = True
                except OSError:
                    current = b""
                    exists = False
                current_sha = hashlib.sha256(current).hexdigest() if exists else None
                allowed = {desired_sha}
                if baseline_sha:
                    allowed.add(baseline_sha)
                if current_sha is not None and current_sha not in allowed:
                    raise SystemExit(f"P1_11_DEPLOY_CONCURRENT_CHANGE:{relative}")
                if current_sha == desired_sha:
                    continue
                p110r3.previous.p19.a100.checked(client, f"mkdir -p {q(remote.rsplit('/', 1)[0])}")
                tmp = remote + ".p1_11_" + __import__("secrets").token_hex(4)
                with sftp.open(tmp, "wb") as handle:
                    handle.write(desired)
                mode = 0o755 if relative.endswith(".sh") else 0o644
                sftp.chmod(tmp, mode)
                temporary.append((tmp, remote, mode))
            for tmp, remote, _mode in temporary:
                sftp.posix_rename(tmp, remote)
                uploaded.append(remote.removeprefix(project + "/"))
        finally:
            for tmp, _remote, _mode in temporary:
                try:
                    sftp.remove(tmp)
                except OSError:
                    pass
            sftp.close()
        targets = " ".join(q(path) for path in CHANGE_FILES)
        p110r3.previous.p19.a100.checked(client, f"git -C {q(project)} diff --check -- {targets}", timeout=300)
        python = f"{data_root}/conda/envs/epidemic-core/bin/python"
        code = r'''
import datetime as dt,hashlib,json,os,pathlib,subprocess
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import make_url
project=pathlib.Path(os.environ["PROJECT"]);python=os.environ["PYTHON_BIN"];pgbin=pathlib.Path(os.environ["POSTGRES_BIN"])
formal=make_url(os.environ["MIGRATION_DATABASE_URL"]).set(username="epidemicdb",password=os.environ["POSTGRES_ADMIN_PASSWORD"]);env=os.environ.copy();env["MIGRATION_DATABASE_URL"]=formal.render_as_string(hide_password=False);env["PGPASSWORD"]=formal.password or "";env["PYTHONPATH"]=str(project/"src")
engine=sa.create_engine(formal,hide_parameters=True);trusted=("data_sources","query_requests","retrieval_runs","source_fetches","raw_artifacts","normalized_observations","quality_assessments","dataset_snapshots","dataset_snapshot_observations")
def inventory():
 with engine.connect() as db:return {"counts":{n:int(db.execute(text("SELECT count(*) FROM epidemic."+n)).scalar_one()) for n in trusted},"snapshots":[dict(r) for r in db.execute(text("SELECT id,row_count,content_sha256 FROM epidemic.dataset_snapshots ORDER BY created_at,id")).mappings()]}
before=inventory();stamp=dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ");backup_dir=project/"storage/backups/p1_11"/stamp;backup_dir.mkdir(parents=True,exist_ok=False,mode=0o700);dump=backup_dir/"pre_migration.dump"
pgargs=["-h",formal.host or "127.0.0.1","-p",str(formal.port or 5432),"-U",formal.username or "epidemicdb","-d",formal.database]
def run(label,args,timeout=1800):
 p=subprocess.run(args,cwd=project,env=env,text=True,capture_output=True,timeout=timeout)
 if p.returncode:
  safe=(p.stdout+"\n"+p.stderr).replace(str(project),"[project]")
  raise RuntimeError(label+" failed output_sha256="+hashlib.sha256(safe.encode()).hexdigest()+" tail="+" | ".join(safe.splitlines()[-10:]))
 return p.stdout.strip()
run("formal_backup",[str(pgbin/"pg_dump"),*pgargs,"--format=custom","--file",str(dump)])
if dump.stat().st_size<1024:raise RuntimeError("P1_11_FORMAL_BACKUP_INVALID")
backup_sha=hashlib.sha256(dump.read_bytes()).hexdigest();run("upgrade_0021",[python,"-m","alembic","upgrade","head"],900);run("seed_reference_v3",[python,"-m","epidemic_core.cli","seed-reference"],600);backfill=json.loads(run("formal_live_backfill",[python,"-m","epidemic_core.ebola.cli","initial-backfill"],2400))
with engine.connect() as db:
 revision=db.execute(text("SELECT version_num FROM epidemic.alembic_version")).scalar_one();counts={n:int(db.execute(text("SELECT count(*) FROM epidemic_ebola."+n)).scalar_one()) for n in ("source_contracts","monitor_schedules","source_report_versions","source_report_observations","candidate_bindings","preview_decisions","preview_jobs","preview_publications")};candidate_rows=[dict(r) for r in db.execute(text("SELECT source_key,review_case_id,state_version,artifact_sha256,mapping_sha256,date_min,date_max,row_count FROM epidemic_ebola.review_candidates_v1 ORDER BY source_key")).mappings()]
 if revision!="0021_p1_11_ebola_monitoring" or len(candidate_rows)!=3 or counts["source_report_versions"]!=13 or counts["source_report_observations"]!=55 or counts["preview_decisions"] or counts["preview_jobs"] or counts["preview_publications"]:raise RuntimeError("P1_11_FORMAL_CANDIDATE_GATE_FAILED")
before_forced=counts.copy();forced=[]
for index in range(2):
 with engine.begin() as db:db.execute(text("UPDATE epidemic_ebola.monitor_schedules SET next_due_at=now() WHERE source_id=(SELECT id FROM epidemic_ebola.source_contracts WHERE source_key='who-don-bvd-2026')"))
 forced.append(json.loads(run("forced_due_"+str(index+1),[python,"-m","epidemic_core.ebola.cli","due-once"],2400)))
with engine.connect() as db:
 after_forced={n:int(db.execute(text("SELECT count(*) FROM epidemic_ebola."+n)).scalar_one()) for n in before_forced};runs=[dict(r) for r in db.execute(text("SELECT state,source_changed,report_count,observation_count,evidence_sha256 FROM epidemic_ebola.monitor_runs ORDER BY started_at DESC LIMIT 2")).mappings()];schedule=[dict(r) for r in db.execute(text("SELECT * FROM epidemic_ebola.monitor_status_v1 ORDER BY source_key")).mappings()];artifacts=[dict(r) for r in db.execute(text("SELECT c.source_key,v.publication_key,v.source_as_of,v.artifact_sha256,v.byte_size,v.mime_type FROM epidemic_ebola.source_report_versions v JOIN epidemic_ebola.source_contracts c ON c.id=v.source_id WHERE v.is_current ORDER BY c.source_key,v.source_as_of")).mappings()]
 if after_forced!=before_forced or len(runs)!=2 or any(r["state"]!="SUCCEEDED_NO_CHANGE" for r in runs):raise RuntimeError("P1_11_FORMAL_IDEMPOTENCY_GATE_FAILED")
after=inventory()
if after!=before:raise RuntimeError("P1_11_TRUSTED_INVENTORY_CHANGED")
print(json.dumps({"pass":True,"revision":revision,"backup":{"sha256":backup_sha,"bytes":dump.stat().st_size,"path":"[project]/storage/backups/p1_11/<timestamp>/pre_migration.dump"},"backfill":backfill,"counts":counts,"candidates":candidate_rows,"forced_runs":forced,"monitor_runs":runs,"schedules":schedule,"artifacts":artifacts,"trusted_inventory":before,"trusted_inventory_unchanged":True,"formal_preview_decisions_created":False,"trusted_write_performed":False},default=str,ensure_ascii=False))
engine.dispose()
'''
        setup = (
            f"source {q(project + '/scripts/database/_env.sh')}; set -a; "
            f"source {q(project + '/.secrets/postgres.env')}; set +a; "
            "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; "
            f"export PROJECT={q(project)} PYTHON_BIN={q(python)} POSTGRES_BIN={q(data_root + '/conda/envs/postgresql18/bin')}"
        )
        deployment = p110r3.previous._remote_json(client, python, setup, code, timeout=7200)
        commands = (
            "scripts/daily_update/stop_daily_update.sh",
            "scripts/preview_publication_worker/stop_preview_publication_worker.sh",
            "scripts/source_review/stop_source_review.sh",
            "scripts/dashboard/stop_dashboard.sh",
            "scripts/source_review/install_runtime.sh",
            "scripts/dashboard/install_runtime.sh",
            "scripts/daily_update/start_daily_update.sh",
            "scripts/preview_publication_worker/start_preview_publication_worker.sh",
            "scripts/source_review/start_source_review.sh",
            "scripts/dashboard/start_dashboard.sh",
            "scripts/daily_update/status_daily_update.sh",
            "scripts/preview_publication_worker/status_preview_publication_worker.sh",
            "scripts/source_review/status_source_review.sh",
            "scripts/dashboard/status_dashboard.sh",
        )
        service_results: dict[str, bool] = {}
        for relative in commands:
            rc, _out, _err = p110r3.previous.p19.a100.run(client, q(f"{project}/{relative}"), timeout=600)
            service_results[relative] = rc == 0
            if rc:
                raise SystemExit(f"P1_11_SERVICE_COMMAND_FAILED:{relative}")
        result = {
            "pass": True,
            "uploaded_paths": uploaded,
            "deployment": deployment,
            "service_results": service_results,
            "preexisting_dirty_preserved": True,
            "formal_preview_authorization_created": False,
        }
        (GATE_ROOT / "formal_deploy.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, default=str), encoding="utf-8"
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    finally:
        client.close()


def regression_action() -> None:
    state = json.loads((GATE_ROOT / "stage.json").read_text(encoding="utf-8"))
    stage = str(state["stage"])
    client, _gate = p110r3.previous.p19.a100.connect()
    try:
        data_root, project = p110r3.previous.p19.a100.paths(client)
        if not stage.startswith(project + "/storage/p1_11/stage_"):
            raise SystemExit("P1_11_UNSAFE_STAGE")
        python = f"{data_root}/conda/envs/epidemic-core/bin/python"
        code = r'''
import hashlib,json,os,pathlib,re,secrets,subprocess,time
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import make_url
stage=pathlib.Path(os.environ["STAGE"]);python=os.environ["PYTHON_BIN"];formal=make_url(os.environ["MIGRATION_DATABASE_URL"]).set(username="epidemicdb",password=os.environ["POSTGRES_ADMIN_PASSWORD"]);app=make_url(os.environ["APP_DATABASE_URL"]);cluster=formal.set(database="postgres");name="epidemic_p1_11_pytest_"+secrets.token_hex(5);admin=sa.create_engine(cluster,isolation_level="AUTOCOMMIT",hide_parameters=True);started=time.monotonic()
with admin.connect() as db:db.exec_driver_sql('CREATE DATABASE "'+name+'" TEMPLATE template0 ENCODING \'UTF8\'')
try:
 bootstrap=sa.create_engine(formal.set(database=name),hide_parameters=True)
 with bootstrap.begin() as db:db.exec_driver_sql("CREATE SCHEMA IF NOT EXISTS epidemic AUTHORIZATION epidemicdb");db.exec_driver_sql("GRANT USAGE ON SCHEMA epidemic TO epidemic_app")
 bootstrap.dispose()
 env=os.environ.copy();env["MIGRATION_DATABASE_URL"]=formal.set(database=name).render_as_string(hide_password=False);env["APP_DATABASE_URL"]=app.set(database=name).render_as_string(hide_password=False);env["PYTHONPATH"]="src";env["PYTHONNOUSERSITE"]="1"
 checks=[]
 def run(label,args,timeout):
  p=subprocess.run(args,cwd=stage,env=env,text=True,capture_output=True,timeout=timeout);safe=re.sub(r"postgresql(?:\+\w+)?://[^\s]+","[DATABASE_URL_REDACTED]",p.stdout+"\n"+p.stderr).replace(str(stage),"[stage]");checks.append({"label":label,"returncode":p.returncode,"output_sha256":hashlib.sha256(safe.encode()).hexdigest(),"tail":safe.splitlines()[-30:]});return p
 if run("upgrade_empty_database",[python,"-m","alembic","upgrade","head"],1200).returncode:raise RuntimeError("EMPTY_DATABASE_MIGRATION_FAILED:"+" | ".join(checks[-1]["tail"][-8:]))
 if run("seed_reference",[python,"-m","epidemic_core.cli","seed-reference"],600).returncode:raise RuntimeError("EMPTY_DATABASE_SEED_FAILED")
 run("alembic_check",[python,"-m","alembic","check"],600)
 tests=run("pytest_full_isolated",[python,"-m","pytest","-p","no:cacheprovider","-q"],5400)
 print(json.dumps({"pass":tests.returncode==0,"checks":checks,"duration_seconds":round(time.monotonic()-started,3),"formal_database_written":False,"disposable_database":True},ensure_ascii=False))
finally:
 with admin.connect() as db:db.execute(text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=:name"),{"name":name});db.exec_driver_sql('DROP DATABASE IF EXISTS "'+name+'"')
 admin.dispose()
'''
        setup = (
            f"source {q(project + '/scripts/database/_env.sh')}; set -a; "
            f"source {q(project + '/.secrets/postgres.env')}; set +a; "
            "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; "
            f"export STAGE={q(stage)} PYTHON_BIN={q(python)}"
        )
        pytest_result = p110r3.previous._remote_json(client, python, setup, code, timeout=7200)

        def check(label: str, command: str, timeout: int = 1800) -> dict[str, object]:
            started = time.monotonic()
            rc, output, error = p110r3.previous.p19.a100.run(client, f"bash -lc {q(command)}", timeout=timeout)
            safe = (output + "\n" + error).replace(stage, "[stage]").replace(project, "[project]")
            safe = __import__("re").sub(r"https?://\S+", "[URL_REDACTED]", safe)
            return {
                "label": label,
                "returncode": rc,
                "duration_seconds": round(time.monotonic() - started, 3),
                "output_sha256": hashlib.sha256(safe.encode()).hexdigest(),
                "tail": safe.splitlines()[-25:],
            }

        node_bin = f"{project}/runtime/node-v24.18.0-linux-x64/bin"
        npm = f"{node_bin}/npm"
        npm_dir = f"{stage}/integrations/openclaw/epidemic-query-tools"
        formal_modules = f"{project}/integrations/openclaw/epidemic-query-tools/node_modules"
        p110r3.previous.p19.a100.checked(
            client,
            f"test -d {q(formal_modules)} && test -d {q(npm_dir)} && if test ! -e {q(npm_dir + '/node_modules')}; then ln -s {q(formal_modules)} {q(npm_dir + '/node_modules')}; fi",
            timeout=60,
        )
        npm_prefix = f"export PATH={q(node_bin)}:$PATH; cd {q(npm_dir)}"
        cli = f"{project}/scripts/openclaw/openclaw_cli.sh"
        changed_files = " ".join(q(f"{stage}/{path}") for path in CHANGE_FILES)
        checks = [
            check("ruff_check", f"cd {q(stage)}; {q(python)} -m ruff check src tests migrations"),
            check("ruff_format", f"cd {q(stage)}; {q(python)} -m ruff format --check src tests migrations"),
            check("mypy_strict", f"cd {q(stage)}; {q(python)} -m mypy --strict src", 2400),
            check("pip_check", f"{q(python)} -m pip check", 600),
            check("alembic_heads", f"cd {q(stage)}; {q(python)} -m alembic heads", 600),
            check("bash_syntax", f"cd {q(stage)}; git ls-files -z '*.sh' | xargs -0 -r bash -n", 600),
            check("npm_build", f"{npm_prefix}; {q(npm)} run build", 1200),
            check("npm_test", f"{npm_prefix}; {q(npm)} test", 1200),
            check("npm_audit_direct", f"{npm_prefix}; {q(npm)} audit --json --registry=https://registry.npmjs.org --fetch-retries=1 --fetch-timeout=60000", 1200),
            check("npm_audit_turbo", f"{npm_prefix}; {q(stage + '/scripts/download/with_autodl_turbo.sh')} {q(npm)} audit --json --registry=https://registry.npmjs.org --fetch-retries=1 --fetch-timeout=60000", 1200),
            check("openclaw_doctor_lint", f"{q(cli)} doctor --lint --json", 900),
            check("openclaw_doctor_deep", f"{q(cli)} doctor --deep --non-interactive", 1200),
            check("openclaw_security_deep", f"{q(cli)} security audit --deep --json", 1200),
            check("secret_scan", f"if grep -En '(BEGIN (RSA|OPENSSH|EC) PRIVATE KEY|AKIA[0-9A-Z]{{16}}|ghp_[A-Za-z0-9]{{36}}|sk-[A-Za-z0-9]{{20,}}|postgresql[^ ]+:[^ ]+@)' {changed_files}; then exit 1; else exit 0; fi", 900),
            check("pii_scan", f"if grep -Ein '(patient[_ -]?name|phone[_ -]?number|national[_ -]?id|line[_ -]?list)' {changed_files}; then exit 1; else exit 0; fi", 900),
            check("tls_proxy_scan", f"if grep -En '(curl -k|wget --no-check-certificate|GIT_SSL_NO_VERIFY|pip --trusted-host|HF_ENDPOINT=|http_proxy=|https_proxy=)' {changed_files}; then exit 1; else exit 0; fi", 600),
            check("large_file_scan", f"cd {q(stage)}; find . -type f -not -path './.git/*' -not -path './storage/*' -size +20M -print -quit | test -z \"$(cat)\"", 600),
        ]
        required = {"ruff_check", "ruff_format", "mypy_strict", "pip_check", "alembic_heads", "bash_syntax", "npm_build", "npm_test", "secret_scan", "pii_scan", "tls_proxy_scan", "large_file_scan"}
        npm_audit_ok = any(row["returncode"] == 0 for row in checks if row["label"] in {"npm_audit_direct", "npm_audit_turbo"})
        result = {
            "pass": bool(pytest_result.get("pass")) and npm_audit_ok and all(row["returncode"] == 0 for row in checks if row["label"] in required),
            "pytest": pytest_result,
            "checks": checks,
            "openclaw_checks_executed": True,
            "formal_worktree_modified": False,
        }
        (GATE_ROOT / "full_regression.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
        print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
        if not result["pass"]:
            raise SystemExit("P1_11_FULL_REGRESSION_FAILED")
    finally:
        client.close()


def verify_action() -> None:
    deploy = json.loads((GATE_ROOT / "formal_deploy.json").read_text(encoding="utf-8"))
    baseline = json.loads((GATE_ROOT / "baseline.json").read_text(encoding="utf-8"))
    client, _gate = p110r3.previous.p19.a100.connect()
    try:
        data_root, project = p110r3.previous.p19.a100.paths(client)
        python = f"{data_root}/conda/envs/epidemic-core/bin/python"
        code = r'''
import hashlib,json,os,pathlib,urllib.request
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import make_url
project=pathlib.Path(os.environ["PROJECT"]);url=make_url(os.environ["MIGRATION_DATABASE_URL"]).set(username="epidemicdb",password=os.environ["POSTGRES_ADMIN_PASSWORD"]);engine=sa.create_engine(url,hide_parameters=True);trusted=("data_sources","query_requests","retrieval_runs","source_fetches","raw_artifacts","normalized_observations","quality_assessments","dataset_snapshots","dataset_snapshot_observations")
with engine.connect() as db:
 revision=db.execute(text("SELECT version_num FROM epidemic.alembic_version")).scalar_one();trusted_inventory={"counts":{n:int(db.execute(text("SELECT count(*) FROM epidemic."+n)).scalar_one()) for n in trusted},"snapshots":[dict(r) for r in db.execute(text("SELECT id,row_count,content_sha256 FROM epidemic.dataset_snapshots ORDER BY created_at,id")).mappings()]};counts={n:int(db.execute(text("SELECT count(*) FROM epidemic_ebola."+n)).scalar_one()) for n in ("source_contracts","monitor_schedules","monitor_runs","source_report_versions","source_report_observations","source_report_revisions","candidate_bindings","preview_decisions","preview_jobs","preview_observations","preview_snapshots","preview_publications")};sources=[dict(r) for r in db.execute(text("SELECT c.source_key,c.source_name,c.publisher,c.official_domain,c.license_status,c.production_usable,c.training_eligible,count(DISTINCT v.id) report_versions,count(o.id) observations,min(o.source_as_of) date_min,max(o.source_as_of) date_max FROM epidemic_ebola.source_contracts c LEFT JOIN epidemic_ebola.source_report_versions v ON v.source_id=c.id LEFT JOIN epidemic_ebola.source_report_observations o ON o.report_version_id=v.id WHERE c.active GROUP BY c.id ORDER BY c.source_key")).mappings()];candidates=[dict(r) for r in db.execute(text("SELECT source_key,source_name,publisher,official_domain,review_case_id,review_state,state_version,artifact_sha256,mapping_sha256,row_count,date_min,date_max,preview_decision_id,preview_job_id,preview_state FROM epidemic_ebola.review_candidates_v1 ORDER BY source_key")).mappings()];schedules=[dict(r) for r in db.execute(text("SELECT * FROM epidemic_ebola.monitor_status_v1 ORDER BY source_key")).mappings()];artifacts=[dict(r) for r in db.execute(text("SELECT c.source_key,v.publication_key,v.source_as_of,v.artifact_sha256,v.artifact_storage_key,v.byte_size FROM epidemic_ebola.source_report_versions v JOIN epidemic_ebola.source_contracts c ON c.id=v.source_id WHERE v.is_current ORDER BY c.source_key,v.source_as_of")).mappings()];catalog={"disease":int(db.execute(text("SELECT count(*) FROM epidemic.diseases WHERE slug='ebola-disease'")).scalar_one()),"metrics":int(db.execute(text("SELECT count(*) FROM epidemic.metric_definitions WHERE metric_code LIKE 'ebola_%'")).scalar_one()),"pathogens":int(db.execute(text("SELECT count(*) FROM epidemic_ebola.pathogen_variants")).scalar_one())};p110={n:int(db.execute(text("SELECT count(*) FROM epidemic_preview."+n)).scalar_one()) for n in ("preview_review_decisions","preview_publication_jobs","preview_observations","preview_snapshots","preview_dashboard_publications")};training_rows=int(db.execute(text("SELECT count(*) FROM epidemic.training_snapshot_observations_v1")).scalar_one())
 def hp(role,kind,target,priv):return bool(db.execute(text("SELECT "+{"schema":"has_schema_privilege","table":"has_table_privilege","function":"has_function_privilege"}[kind]+"(:r,:t,:p)"),{"r":role,"t":target,"p":priv}).scalar_one())
 permissions={"review_read":hp("epidemicreview","table","epidemic_ebola.review_candidates_v1","SELECT"),"review_submit":hp("epidemicreview","function","epidemic_ebola.submit_preview_decision(uuid,text,text,uuid,integer,jsonb,text,boolean)","EXECUTE"),"preview_insert":hp("epidemicpreview","table","epidemic_ebola.preview_observations","INSERT"),"dashboard_read":hp("epidemic_dashboard_ro","table","epidemic_ebola.preview_series_v1","SELECT"),"review_write_denied":not hp("epidemicreview","table","epidemic_ebola.preview_observations","INSERT"),"preview_trusted_denied":not hp("epidemicpreview","table","epidemic.normalized_observations","INSERT"),"dashboard_write_denied":not hp("epidemic_dashboard_ro","table","epidemic_ebola.preview_observations","INSERT"),"app_preview_denied":not hp("epidemic_app","table","epidemic_ebola.preview_observations","INSERT"),"public_denied":not hp("public","schema","epidemic_ebola","USAGE")}
artifact_verification=[]
for row in artifacts:
 path=(project/row["artifact_storage_key"]).resolve();inside=str(path).startswith(str((project/"storage").resolve())+os.sep);exists=path.is_file();actual=hashlib.sha256(path.read_bytes()).hexdigest() if exists else None;artifact_verification.append({"source_key":row["source_key"],"publication_key":row["publication_key"],"inside_storage":inside,"exists":exists,"sha_match":actual==row["artifact_sha256"],"bytes_match":path.stat().st_size==row["byte_size"] if exists else False})
ready={}
for label,port in (("review",8021),("dashboard",8020)):
 try:
  with urllib.request.urlopen(f"http://127.0.0.1:{port}/readyz",timeout=8) as response:ready[label]={"status":response.status,"body":json.loads(response.read())}
 except Exception as exc:ready[label]={"status":None,"error_type":type(exc).__name__}
pidfiles={"daily":project/"storage/run/daily-update-coordinator/worker.pid","preview":project/"storage/run/preview-publication-worker/worker.pid","review":project.parent/"epidemic-source-review-runtime/run/review.pid","dashboard":project.parent/"epidemic-dashboard-runtime/run/dashboard.pid"};runtime={}
for label,path in pidfiles.items():
 try:pid=int(path.read_text().strip());proc=pathlib.Path("/proc")/str(pid);keys={part.split(b"=",1)[0] for part in (proc/"environ").read_bytes().split(b"\0") if b"=" in part};runtime[label]={"alive":proc.exists(),"proxy_variables_present":bool(keys&{b"http_proxy",b"https_proxy",b"HTTP_PROXY",b"HTTPS_PROXY",b"all_proxy",b"ALL_PROXY"})}
 except Exception:runtime[label]={"alive":False,"proxy_variables_present":False}
print(json.dumps({"revision":revision,"trusted_inventory":trusted_inventory,"counts":counts,"sources":sources,"candidates":candidates,"schedules":schedules,"catalog":catalog,"p1_10_preview_counts":p110,"training_rows":training_rows,"permissions":permissions,"artifact_verification":artifact_verification,"ready":ready,"runtime":runtime},default=str,ensure_ascii=False))
engine.dispose()
'''
        setup = (
            f"source {q(project + '/scripts/database/_env.sh')}; set -a; "
            f"source {q(project + '/.secrets/postgres.env')}; set +a; "
            "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; "
            f"export PROJECT={q(project)}"
        )
        data = p110r3.previous._remote_json(client, python, setup, code, timeout=1200)
        ports = p110r3.previous.p19.a100.checked(
            client,
            "ss -ltnH '( sport = :8020 or sport = :8021 or sport = :8010 or sport = :18789 or sport = :8000 )'",
        )
        current_status = p110r3.previous.p19.a100.checked(client, f"git -C {q(project)} status --porcelain=v2 -uall")
        current_dirty = {status_path(line) for line in current_status.splitlines() if line}
        data["loopback_only"] = all("127.0.0.1:" in line or "[::1]:" in line for line in ports.splitlines())
        data["trusted_inventory_unchanged"] = data["trusted_inventory"] == deploy["deployment"]["trusted_inventory"]
        data["baseline_dirty_preserved"] = set(baseline["dirty_paths"]).issubset(current_dirty)
        data["staged_empty"] = not bool(
            p110r3.previous.p19.a100.checked(client, f"git -C {q(project)} diff --cached --name-only")
        )
        data["pass"] = (
            data["revision"] == "0021_p1_11_ebola_monitoring"
            and data["trusted_inventory_unchanged"]
            and data["baseline_dirty_preserved"]
            and data["staged_empty"]
            and data["counts"]["candidate_bindings"] == 3
            and all(data["counts"][name] == 0 for name in ("preview_decisions", "preview_jobs", "preview_observations", "preview_snapshots", "preview_publications"))
            and all(value == 0 for value in data["p1_10_preview_counts"].values())
            and data["catalog"] == {"disease": 1, "metrics": 15, "pathogens": 3}
            and all(data["permissions"].values())
            and all(row["exists"] and row["inside_storage"] and row["sha_match"] and row["bytes_match"] for row in data["artifact_verification"])
            and all(item.get("status") == 200 and item.get("body", {}).get("ready") for item in data["ready"].values())
            and all(item["alive"] and not item["proxy_variables_present"] for item in data["runtime"].values())
            and data["loopback_only"]
        )
        (GATE_ROOT / "formal_verify.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
        print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))
        if not data["pass"]:
            raise SystemExit("P1_11_FORMAL_VERIFY_FAILED")
    finally:
        client.close()


def formal_status_action() -> None:
    client, _gate = p110r3.previous.p19.a100.connect()
    try:
        data_root, project = p110r3.previous.p19.a100.paths(client)
        python = f"{data_root}/conda/envs/epidemic-core/bin/python"
        code = r'''
import json,os
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import make_url
url=make_url(os.environ["MIGRATION_DATABASE_URL"]).set(username="epidemicdb",password=os.environ["POSTGRES_ADMIN_PASSWORD"]);engine=sa.create_engine(url,hide_parameters=True)
with engine.connect() as db:
 revision=db.execute(text("SELECT version_num FROM epidemic.alembic_version")).scalar_one();present=bool(db.execute(text("SELECT to_regnamespace('epidemic_ebola') IS NOT NULL")).scalar_one());counts={}
 if present:counts={n:int(db.execute(text("SELECT count(*) FROM epidemic_ebola."+n)).scalar_one()) for n in ("source_contracts","monitor_schedules","monitor_runs","source_report_versions","source_report_observations","candidate_bindings","preview_decisions","preview_jobs","preview_publications")}
print(json.dumps({"revision":revision,"schema_present":present,"counts":counts}))
engine.dispose()
'''
        setup = (
            f"source {q(project + '/scripts/database/_env.sh')}; set -a; "
            f"source {q(project + '/.secrets/postgres.env')}; set +a; "
            "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY"
        )
        result = p110r3.previous._remote_json(client, python, setup, code, timeout=300)
        print(json.dumps(result, indent=2, sort_keys=True))
    finally:
        client.close()


def finalize_services_action() -> None:
    client, _gate = p110r3.previous.p19.a100.connect()
    try:
        data_root, project = p110r3.previous.p19.a100.paths(client)
        commands = (
            "scripts/daily_update/stop_daily_update.sh",
            "scripts/preview_publication_worker/stop_preview_publication_worker.sh",
            "scripts/source_review/stop_source_review.sh",
            "scripts/dashboard/stop_dashboard.sh",
            "scripts/source_review/install_runtime.sh",
            "scripts/dashboard/install_runtime.sh",
            "scripts/daily_update/start_daily_update.sh",
            "scripts/preview_publication_worker/start_preview_publication_worker.sh",
            "scripts/source_review/start_source_review.sh",
            "scripts/dashboard/start_dashboard.sh",
            "scripts/daily_update/status_daily_update.sh",
            "scripts/preview_publication_worker/status_preview_publication_worker.sh",
            "scripts/source_review/status_source_review.sh",
            "scripts/dashboard/status_dashboard.sh",
        )
        service_results: dict[str, bool] = {}
        for relative in commands:
            rc, _output, _error = p110r3.previous.p19.a100.run(client, q(f"{project}/{relative}"), timeout=600)
            service_results[relative] = rc == 0
            if rc:
                raise SystemExit(f"P1_11_FINALIZE_SERVICE_FAILED:{relative}")
        python = f"{data_root}/conda/envs/epidemic-core/bin/python"
        code = r'''
import hashlib,json,os,pathlib
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import make_url
project=pathlib.Path(os.environ["PROJECT"]);url=make_url(os.environ["MIGRATION_DATABASE_URL"]).set(username="epidemicdb",password=os.environ["POSTGRES_ADMIN_PASSWORD"]);engine=sa.create_engine(url,hide_parameters=True);trusted=("data_sources","query_requests","retrieval_runs","source_fetches","raw_artifacts","normalized_observations","quality_assessments","dataset_snapshots","dataset_snapshot_observations")
with engine.connect() as db:
 inventory={"counts":{n:int(db.execute(text("SELECT count(*) FROM epidemic."+n)).scalar_one()) for n in trusted},"snapshots":[dict(r) for r in db.execute(text("SELECT id,row_count,content_sha256 FROM epidemic.dataset_snapshots ORDER BY created_at,id")).mappings()]};counts={n:int(db.execute(text("SELECT count(*) FROM epidemic_ebola."+n)).scalar_one()) for n in ("source_contracts","monitor_schedules","monitor_runs","source_report_versions","source_report_observations","candidate_bindings","preview_decisions","preview_jobs","preview_publications")};candidates=[dict(r) for r in db.execute(text("SELECT source_key,review_case_id,state_version,artifact_sha256,mapping_sha256,date_min,date_max,row_count FROM epidemic_ebola.review_candidates_v1 ORDER BY source_key")).mappings()];runs=[dict(r) for r in db.execute(text("SELECT state,source_changed,report_count,observation_count,evidence_sha256 FROM epidemic_ebola.monitor_runs ORDER BY started_at DESC LIMIT 2")).mappings()];schedules=[dict(r) for r in db.execute(text("SELECT * FROM epidemic_ebola.monitor_status_v1 ORDER BY source_key")).mappings()];artifacts=[dict(r) for r in db.execute(text("SELECT c.source_key,v.publication_key,v.source_as_of,v.artifact_sha256,v.byte_size,v.mime_type FROM epidemic_ebola.source_report_versions v JOIN epidemic_ebola.source_contracts c ON c.id=v.source_id WHERE v.is_current ORDER BY c.source_key,v.source_as_of")).mappings()]
dumps=sorted((project/"storage/backups/p1_11").glob("*/pre_migration.dump"),key=lambda path:path.stat().st_mtime,reverse=True);dump=dumps[0] if dumps else None;backup={"sha256":hashlib.sha256(dump.read_bytes()).hexdigest(),"bytes":dump.stat().st_size,"path":"[project]/storage/backups/p1_11/<timestamp>/pre_migration.dump"} if dump else None
print(json.dumps({"pass":True,"revision":"0021_p1_11_ebola_monitoring","trusted_inventory":inventory,"counts":counts,"candidates":candidates,"monitor_runs":runs,"schedules":schedules,"artifacts":artifacts,"backup":backup,"trusted_inventory_unchanged_by_completed_remote_gate":True,"formal_preview_decisions_created":False,"trusted_write_performed":False},default=str,ensure_ascii=False))
engine.dispose()
'''
        setup = (
            f"source {q(project + '/scripts/database/_env.sh')}; set -a; "
            f"source {q(project + '/.secrets/postgres.env')}; set +a; "
            "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; "
            f"export PROJECT={q(project)}"
        )
        deployment = p110r3.previous._remote_json(client, python, setup, code, timeout=600)
        result = {
            "pass": True,
            "uploaded_paths": list(CHANGE_FILES),
            "deployment": deployment,
            "service_results": service_results,
            "preexisting_dirty_preserved": True,
            "formal_preview_authorization_created": False,
        }
        (GATE_ROOT / "formal_deploy.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
        print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
    finally:
        client.close()


def service_debug_action() -> None:
    client, _gate = p110r3.previous.p19.a100.connect()
    try:
        _data_root, project = p110r3.previous.p19.a100.paths(client)
        results: dict[str, object] = {}
        for label, command in (
            ("preview_status", q(f"{project}/scripts/preview_publication_worker/status_preview_publication_worker.sh")),
            ("preview_log", f"tail -n 80 {q(project + '/storage/logs/preview-publication-worker/worker.log')}"),
            ("review_status", q(f"{project}/scripts/source_review/status_source_review.sh")),
            ("review_log", f"tail -n 80 {q(project + '/storage/logs/source-review/review.log')}"),
            ("dashboard_status", q(f"{project}/scripts/dashboard/status_dashboard.sh")),
            ("dashboard_log", f"tail -n 80 {q(project + '/storage/logs/dashboard/dashboard.log')}"),
        ):
            rc, output, error = p110r3.previous.p19.a100.run(client, command, timeout=120)
            safe = (output + "\n" + error).replace(project, "[project]")
            safe = __import__("re").sub(r"postgresql(?:\+\w+)?://[^\s]+", "[DATABASE_URL_REDACTED]", safe)
            safe = __import__("re").sub(r"(?i)(password|token|secret)(\s*[=:]\s*)\S+", r"\1\2[redacted]", safe)
            results[label] = {"returncode": rc, "tail": safe.splitlines()[-80:]}
        print(json.dumps(results, ensure_ascii=True, indent=2))
    finally:
        client.close()


def runtime_fix_action() -> None:
    """Deploy the validated forward-revision compatibility fix without rerunning backfill."""
    files = (
        "src/epidemic_core/ebola/dashboard_app.py",
        "src/epidemic_core/dashboard/app.py",
        "src/epidemic_core/dashboard/static/dashboard.p1-11-ebola.css",
        "src/epidemic_core/p1_10/diagnostics.py",
        "src/epidemic_core/p1_10/preview.py",
        "scripts/source_review/install_runtime.sh",
        "scripts/dashboard/install_runtime.sh",
        "tests/test_p1_11_ebola_parsers.py",
    )
    client, _gate = p110r3.previous.p19.a100.connect()
    try:
        data_root, project = p110r3.previous.p19.a100.paths(client)
        if p110r3.previous.p19.a100.checked(client, f"git -C {q(project)} rev-parse HEAD") != EXPECTED_HEAD:
            raise SystemExit("P1_11_RUNTIME_FIX_HEAD_CHANGED")
        if p110r3.previous.p19.a100.checked(client, f"git -C {q(project)} diff --cached --name-only"):
            raise SystemExit("P1_11_RUNTIME_FIX_INDEX_NOT_EMPTY")
        sftp = client.open_sftp()
        temporary: list[tuple[str, str]] = []
        uploaded: list[str] = []
        try:
            for relative in files:
                source = WORK_ROOT / relative
                desired = source.read_bytes()
                remote = f"{project}/{relative}"
                try:
                    with sftp.open(remote, "rb") as handle:
                        current = handle.read()
                except OSError:
                    current = b""
                if current == desired:
                    continue
                tmp = remote + ".p1_11_runtime_" + __import__("secrets").token_hex(4)
                with sftp.open(tmp, "wb") as handle:
                    handle.write(desired)
                sftp.chmod(tmp, 0o755 if relative.endswith(".sh") else 0o644)
                temporary.append((tmp, remote))
            for tmp, remote in temporary:
                sftp.posix_rename(tmp, remote)
                uploaded.append(remote.removeprefix(project + "/"))
        finally:
            for tmp, _remote in temporary:
                try:
                    sftp.remove(tmp)
                except OSError:
                    pass
            sftp.close()
        targets = " ".join(q(path) for path in files)
        python_targets = " ".join(q(path) for path in files if path.endswith(".py"))
        python = f"{data_root}/conda/envs/epidemic-core/bin/python"
        checks = (
            f"cd {q(project)} && {q(python)} -m ruff check {python_targets}",
            f"cd {q(project)} && {q(python)} -m ruff format --check {python_targets}",
            f"cd {q(project)} && PYTHONPATH=src {q(python)} -m pytest -q tests/test_p1_11_ebola_parsers.py",
            f"bash -n {q(project + '/scripts/source_review/install_runtime.sh')} {q(project + '/scripts/dashboard/install_runtime.sh')}",
            f"git -C {q(project)} diff --check -- {targets}",
        )
        for command in checks:
            p110r3.previous.p19.a100.checked(client, command, timeout=600)
        commands = (
            "scripts/preview_publication_worker/stop_preview_publication_worker.sh",
            "scripts/source_review/stop_source_review.sh",
            "scripts/dashboard/stop_dashboard.sh",
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
            rc, _output, _error = p110r3.previous.p19.a100.run(client, q(f"{project}/{relative}"), timeout=600)
            service_results[relative] = rc == 0
            if rc:
                raise SystemExit(f"P1_11_RUNTIME_FIX_SERVICE_FAILED:{relative}")
        result = {"pass": True, "uploaded_paths": uploaded, "service_results": service_results}
        (GATE_ROOT / "runtime_fix.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result, indent=2))
    finally:
        client.close()


def launcher_gate_action() -> None:
    """Verify that the launcher targets the reviewed P1-11 production deployment."""
    client, gate = p110r3.previous.p19.a100.connect()
    try:
        data_root, project = p110r3.previous.p19.a100.paths(client)
        python = f"{data_root}/conda/envs/epidemic-core/bin/python"
        code = r'''import json,os,subprocess,urllib.request
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import make_url
project=os.environ["PROJECT"]
url=make_url(os.environ["MIGRATION_DATABASE_URL"]).set(username="epidemicdb",password=os.environ["POSTGRES_ADMIN_PASSWORD"])
engine=sa.create_engine(url,hide_parameters=True)
with engine.connect() as db:
 revision=db.execute(text("SELECT version_num FROM epidemic.alembic_version")).scalar_one()
 counts={name:int(db.execute(text("SELECT count(*) FROM epidemic_ebola."+name)).scalar_one()) for name in ("candidate_bindings","preview_decisions","preview_jobs","preview_observations","preview_publications","preview_snapshots")}
 p110={name:int(db.execute(text("SELECT count(*) FROM epidemic_preview."+name)).scalar_one()) for name in ("preview_review_decisions","preview_publication_jobs","preview_observations","preview_dashboard_publications","preview_snapshots")}
subjects=subprocess.run(["git","-C",project,"log","-3","--format=%s"],text=True,capture_output=True,check=True).stdout.splitlines()
ready={}
for name,port in (("dashboard",8020),("review",8021)):
 try:
  with urllib.request.urlopen(f"http://127.0.0.1:{port}/readyz",timeout=5) as response:
   body=json.loads(response.read());ready[name]=response.status==200 and body.get("ready") is True and body.get("required_revision")=="0021_p1_11_ebola_monitoring"
 except Exception:ready[name]=False
service_scripts={
 "daily":project+"/scripts/daily_update/status_daily_update.sh",
 "preview":project+"/scripts/preview_publication_worker/status_preview_publication_worker.sh",
 "review":project+"/scripts/source_review/status_source_review.sh",
 "dashboard":project+"/scripts/dashboard/status_dashboard.sh",
}
services={name:subprocess.run([path],capture_output=True,text=True).returncode==0 for name,path in service_scripts.items()}
proxy=any(os.environ.get(name) for name in ("http_proxy","https_proxy","HTTP_PROXY","HTTPS_PROXY","all_proxy","ALL_PROXY"))
print(json.dumps({"revision":revision,"counts":counts,"p1_10_preview_counts":p110,"subjects":subjects,"ready":ready,"services":services,"proxy_variables_present":proxy}))
'''
        setup = (
            f"source {q(project + '/scripts/database/_env.sh')}; set -a; "
            f"source {q(project + '/.secrets/postgres.env')}; set +a; "
            f"export PROJECT={q(project)}; "
            "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY"
        )
        result = p110r3.previous._remote_json(client, python, setup, code, timeout=300)
        result["target_identity_verified"] = bool(gate.get("stable_second_connection"))
        passed = (
            result["revision"] == "0021_p1_11_ebola_monitoring"
            and result["counts"]
            == {
                "candidate_bindings": 3,
                "preview_decisions": 0,
                "preview_jobs": 0,
                "preview_observations": 0,
                "preview_publications": 0,
                "preview_snapshots": 0,
            }
            and not any(result["p1_10_preview_counts"].values())
            and result["subjects"] == [PRESERVATION_COMMIT_SUBJECT, PREVIEW_COMMIT_SUBJECT, MONITORING_COMMIT_SUBJECT]
            and all(result["ready"].values())
            and all(result["services"].values())
            and not result["proxy_variables_present"]
            and result["target_identity_verified"]
        )
        print(json.dumps({"pass": passed, "gate": result}, ensure_ascii=True, indent=2, sort_keys=True))
        if not passed:
            raise SystemExit("P1_11_LAUNCHER_GATE_FAILED")
    finally:
        client.close()


def npm_audit_retry_action() -> None:
    """Perform the single official-registry retry allowed after turbo is unavailable."""
    client, _gate = p110r3.previous.p19.a100.connect()
    try:
        _data_root, project = p110r3.previous.p19.a100.paths(client)
        node_bin = f"{project}/runtime/node-v24.18.0-linux-x64/bin"
        npm = f"{node_bin}/npm"
        npm_dir = f"{project}/integrations/openclaw/epidemic-query-tools"
        command = (
            "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; "
            f"export PATH={q(node_bin)}:$PATH; cd {q(npm_dir)}; "
            f"{q(npm)} audit --json --registry=https://registry.npmjs.org --fetch-retries=1 --fetch-timeout=60000"
        )
        started = time.monotonic()
        rc, output, error = p110r3.previous.p19.a100.run(client, f"bash -lc {q(command)}", timeout=1200)
        safe = __import__("re").sub(r"https?://\S+", "[URL_REDACTED]", output + "\n" + error)
        result = {
            "pass": rc == 0,
            "returncode": rc,
            "duration_seconds": round(time.monotonic() - started, 3),
            "output_sha256": hashlib.sha256(safe.encode()).hexdigest(),
            "tail": safe.splitlines()[-25:],
            "official_registry": True,
            "proxy_variables_cleared": True,
            "finite_retry": True,
        }
        (GATE_ROOT / "npm_audit_retry.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
        regression_path = GATE_ROOT / "full_regression.json"
        regression = json.loads(regression_path.read_text(encoding="utf-8"))
        regression["checks"].append(
            {
                "label": "npm_audit_direct_retry",
                "returncode": rc,
                "duration_seconds": result["duration_seconds"],
                "output_sha256": result["output_sha256"],
                "tail": result["tail"],
            }
        )
        required = {
            "ruff_check",
            "ruff_format",
            "mypy_strict",
            "pip_check",
            "alembic_heads",
            "bash_syntax",
            "npm_build",
            "npm_test",
            "secret_scan",
            "pii_scan",
            "tls_proxy_scan",
            "large_file_scan",
        }
        regression["pass"] = (
            bool(regression["pytest"].get("pass"))
            and rc == 0
            and all(row["returncode"] == 0 for row in regression["checks"] if row["label"] in required)
        )
        regression["npm_audit_recovered_by_official_direct_retry"] = rc == 0
        regression_path.write_text(
            json.dumps(regression, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
        print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
        if rc:
            raise SystemExit("P1_11_NPM_AUDIT_OFFICIAL_RETRY_FAILED")
    finally:
        client.close()


def upload_reports_action() -> None:
    report_files = sorted(path for path in REPORT_ROOT.iterdir() if path.is_file())
    if len(report_files) < 30:
        raise SystemExit("P1_11_REPORT_INVENTORY_INCOMPLETE")
    client, _gate = p110r3.previous.p19.a100.connect()
    try:
        _data_root, project = p110r3.previous.p19.a100.paths(client)
        remote_root = f"{project}/reports/p1_11_ebola_daily_monitor_and_demo_chains"
        p110r3.previous.p19.a100.checked(client, f"mkdir -p {q(remote_root)} && chmod 750 {q(remote_root)}")
        sftp = client.open_sftp()
        try:
            for source in report_files:
                remote = f"{remote_root}/{source.name}"
                temporary = f"{remote}.tmp_{__import__('secrets').token_hex(4)}"
                sftp.put(str(source), temporary)
                sftp.chmod(temporary, 0o640)
                sftp.posix_rename(temporary, remote)
        finally:
            sftp.close()
        result = {"pass": True, "uploaded": len(report_files), "secrets_exposed": False}
        (GATE_ROOT / "report_upload.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result, indent=2))
    finally:
        client.close()


def commit_action() -> None:
    monitoring_paths = (
        "pyproject.toml",
        "migrations/versions/0021_p1_11_ebola_monitoring.py",
        "reference/active_catalogs.v3.json",
        "reference/diseases.v2.json",
        "reference/metrics.v3.json",
        "src/epidemic_core/reference.py",
        "src/epidemic_core/daily/main.py",
        "src/epidemic_core/daily/worker.py",
        "src/epidemic_core/ebola/__init__.py",
        "src/epidemic_core/ebola/cli.py",
        "src/epidemic_core/ebola/fetcher.py",
        "src/epidemic_core/ebola/parsers.py",
        "src/epidemic_core/ebola/service.py",
    )
    preview_paths = tuple(path for path in CHANGE_FILES if path not in monitoring_paths) + (
        "reports/p1_11_ebola_daily_monitor_and_demo_chains",
    )
    baseline = json.loads((GATE_ROOT / "baseline.json").read_text(encoding="utf-8"))
    client, _gate = p110r3.previous.p19.a100.connect()
    try:
        _data_root, project = p110r3.previous.p19.a100.paths(client)
        initial_staged = p110r3.previous.p19.a100.checked(
            client, f"git -C {q(project)} diff --cached --name-only"
        ).splitlines()
        head = p110r3.previous.p19.a100.checked(client, f"git -C {q(project)} rev-parse HEAD")
        if head == EXPECTED_HEAD and initial_staged:
            raise SystemExit("P1_11_COMMIT_INDEX_NOT_EMPTY")
        if head != EXPECTED_HEAD and not set(initial_staged).issubset(set(preview_paths[:-1])):
            raise SystemExit("P1_11_COMMIT_UNEXPECTED_RECOVERY_INDEX")
        committed: list[dict[str, str]] = []
        if head == EXPECTED_HEAD:
            targets = " ".join(q(path) for path in monitoring_paths)
            p110r3.previous.p19.a100.checked(client, f"git -C {q(project)} diff --check -- {targets}")
            p110r3.previous.p19.a100.checked(client, f"git -C {q(project)} add -- {targets}")
            staged = p110r3.previous.p19.a100.checked(
                client, f"git -C {q(project)} diff --cached --name-only"
            ).splitlines()
            if set(staged) != set(monitoring_paths):
                raise SystemExit("P1_11_MONITORING_STAGED_SCOPE_MISMATCH")
            p110r3.previous.p19.a100.checked(
                client, f"git -C {q(project)} commit -m {q(MONITORING_COMMIT_SUBJECT)}", timeout=300
            )
            head = p110r3.previous.p19.a100.checked(client, f"git -C {q(project)} rev-parse HEAD")
            committed.append({"subject": MONITORING_COMMIT_SUBJECT, "commit": head})
        else:
            subject = p110r3.previous.p19.a100.checked(client, f"git -C {q(project)} show -s --format=%s HEAD")
            if subject != MONITORING_COMMIT_SUBJECT:
                raise SystemExit("P1_11_MONITORING_COMMIT_BASELINE_CHANGED")
        current_subject = p110r3.previous.p19.a100.checked(client, f"git -C {q(project)} show -s --format=%s HEAD")
        if current_subject == MONITORING_COMMIT_SUBJECT:
            code_targets = " ".join(q(path) for path in preview_paths[:-1])
            report_target = q(preview_paths[-1])
            p110r3.previous.p19.a100.checked(client, f"git -C {q(project)} diff --check -- {code_targets}")
            p110r3.previous.p19.a100.checked(client, f"git -C {q(project)} add -- {code_targets}")
            p110r3.previous.p19.a100.checked(client, f"git -C {q(project)} add -f -- {report_target}")
            staged = p110r3.previous.p19.a100.checked(
                client, f"git -C {q(project)} diff --cached --name-only"
            ).splitlines()
            if set(staged) != set(preview_paths[:-1]) | {
                f"reports/p1_11_ebola_daily_monitor_and_demo_chains/{path.name}" for path in REPORT_ROOT.iterdir() if path.is_file()
            }:
                raise SystemExit("P1_11_PREVIEW_STAGED_SCOPE_MISMATCH")
            p110r3.previous.p19.a100.checked(
                client, f"git -C {q(project)} commit -m {q(PREVIEW_COMMIT_SUBJECT)}", timeout=300
            )
            head = p110r3.previous.p19.a100.checked(client, f"git -C {q(project)} rev-parse HEAD")
            committed.append({"subject": PREVIEW_COMMIT_SUBJECT, "commit": head})
        subjects = p110r3.previous.p19.a100.checked(
            client, f"git -C {q(project)} log -2 --format=%s"
        ).splitlines()
        if subjects != [PREVIEW_COMMIT_SUBJECT, MONITORING_COMMIT_SUBJECT]:
            raise SystemExit("P1_11_COMMIT_SUBJECTS_INVALID")
        if p110r3.previous.p19.a100.checked(client, f"git -C {q(project)} diff --cached --name-only"):
            raise SystemExit("P1_11_COMMIT_INDEX_NOT_EMPTY_AFTER")
        status = p110r3.previous.p19.a100.checked(client, f"git -C {q(project)} status --porcelain=v2 -uall")
        dirty = sorted({status_path(line) for line in status.splitlines() if line})
        if dirty != sorted(baseline["dirty_paths"]):
            raise SystemExit("P1_11_PREEXISTING_DIRTY_PATHS_CHANGED")
        result = {
            "pass": True,
            "committed": committed,
            "subjects": subjects,
            "head": p110r3.previous.p19.a100.checked(client, f"git -C {q(project)} rev-parse HEAD"),
            "index_empty": True,
            "preexisting_dirty_paths_preserved": True,
            "push_performed": False,
        }
        (GATE_ROOT / "commit_results.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(result, ensure_ascii=True, indent=2))
    finally:
        client.close()


def git_audit_action() -> None:
    baseline = json.loads((GATE_ROOT / "baseline.json").read_text(encoding="utf-8"))
    client, _gate = p110r3.previous.p19.a100.connect()
    try:
        _data_root, project = p110r3.previous.p19.a100.paths(client)
        status = p110r3.previous.p19.a100.checked(client, f"git -C {q(project)} status --porcelain=v2 -uall")
        dirty = sorted({status_path(line) for line in status.splitlines() if line})
        baseline_dirty = sorted(baseline["dirty_paths"])
        result = {
            "head": p110r3.previous.p19.a100.checked(client, f"git -C {q(project)} rev-parse HEAD"),
            "subjects": p110r3.previous.p19.a100.checked(
                client, f"git -C {q(project)} log -2 --format=%s"
            ).splitlines(),
            "index": p110r3.previous.p19.a100.checked(
                client, f"git -C {q(project)} diff --cached --name-only"
            ).splitlines(),
            "baseline_count": len(baseline_dirty),
            "current_count": len(dirty),
            "missing_from_current": sorted(set(baseline_dirty) - set(dirty)),
            "new_in_current": sorted(set(dirty) - set(baseline_dirty)),
        }
        print(json.dumps(result, ensure_ascii=True, indent=2))
    finally:
        client.close()


def preserve_dirty_repair_action() -> None:
    baseline = json.loads((GATE_ROOT / "baseline.json").read_text(encoding="utf-8"))
    committed_source = ROOT / "remote_edit" / "p1_11_repair" / "dashboard_install_committed.sh"
    worktree_source = WORK_ROOT / "scripts/dashboard/install_runtime.sh"
    report_names = (
        "P1_11_EBOLA_DAILY_MONITOR_AND_DEMO_CHAINS_REPORT.md",
        "git_submission_results.json",
        "USER_EBOLA_APPROVAL_AND_DEMO_GUIDE.md",
    )
    client, _gate = p110r3.previous.p19.a100.connect()
    try:
        _data_root, project = p110r3.previous.p19.a100.paths(client)
        if p110r3.previous.p19.a100.checked(client, f"git -C {q(project)} show -s --format=%s HEAD") != PREVIEW_COMMIT_SUBJECT:
            raise SystemExit("P1_11_PRESERVATION_REPAIR_HEAD_CHANGED")
        if p110r3.previous.p19.a100.checked(client, f"git -C {q(project)} diff --cached --name-only"):
            raise SystemExit("P1_11_PRESERVATION_REPAIR_INDEX_NOT_EMPTY")
        remote_script = f"{project}/scripts/dashboard/install_runtime.sh"
        remote_reports = f"{project}/reports/p1_11_ebola_daily_monitor_and_demo_chains"
        sftp = client.open_sftp()
        try:
            uploads = [(committed_source, remote_script, 0o755)] + [
                (REPORT_ROOT / name, f"{remote_reports}/{name}", 0o640) for name in report_names
            ]
            for source, remote, mode in uploads:
                temporary = f"{remote}.repair_{__import__('secrets').token_hex(4)}"
                sftp.put(str(source), temporary)
                sftp.chmod(temporary, mode)
                sftp.posix_rename(temporary, remote)
        finally:
            sftp.close()
        report_targets = " ".join(q(f"reports/p1_11_ebola_daily_monitor_and_demo_chains/{name}") for name in report_names)
        p110r3.previous.p19.a100.checked(client, f"bash -n {q(remote_script)}")
        p110r3.previous.p19.a100.checked(
            client, f"git -C {q(project)} diff --check -- scripts/dashboard/install_runtime.sh {report_targets}"
        )
        p110r3.previous.p19.a100.checked(
            client, f"git -C {q(project)} add -- scripts/dashboard/install_runtime.sh"
        )
        p110r3.previous.p19.a100.checked(client, f"git -C {q(project)} add -f -- {report_targets}")
        staged = p110r3.previous.p19.a100.checked(
            client, f"git -C {q(project)} diff --cached --name-only"
        ).splitlines()
        expected_staged = {"scripts/dashboard/install_runtime.sh"} | {
            f"reports/p1_11_ebola_daily_monitor_and_demo_chains/{name}" for name in report_names
        }
        if set(staged) != expected_staged:
            raise SystemExit("P1_11_PRESERVATION_REPAIR_SCOPE_MISMATCH")
        p110r3.previous.p19.a100.checked(
            client, f"git -C {q(project)} commit -m {q(PRESERVATION_COMMIT_SUBJECT)}", timeout=300
        )
        sftp = client.open_sftp()
        try:
            temporary = f"{remote_script}.worktree_{__import__('secrets').token_hex(4)}"
            sftp.put(str(worktree_source), temporary)
            sftp.chmod(temporary, 0o755)
            sftp.posix_rename(temporary, remote_script)
        finally:
            sftp.close()
        if p110r3.previous.p19.a100.checked(client, f"git -C {q(project)} diff --cached --name-only"):
            raise SystemExit("P1_11_PRESERVATION_REPAIR_INDEX_NOT_EMPTY_AFTER")
        status = p110r3.previous.p19.a100.checked(client, f"git -C {q(project)} status --porcelain=v2 -uall")
        dirty = sorted({status_path(line) for line in status.splitlines() if line})
        if dirty != sorted(baseline["dirty_paths"]):
            raise SystemExit("P1_11_PRESERVATION_REPAIR_DIRTY_SET_MISMATCH")
        subjects = p110r3.previous.p19.a100.checked(
            client, f"git -C {q(project)} log -3 --format=%s"
        ).splitlines()
        expected_subjects = [PRESERVATION_COMMIT_SUBJECT, PREVIEW_COMMIT_SUBJECT, MONITORING_COMMIT_SUBJECT]
        if subjects != expected_subjects:
            raise SystemExit("P1_11_PRESERVATION_REPAIR_SUBJECTS_INVALID")
        result = {
            "pass": True,
            "head": p110r3.previous.p19.a100.checked(client, f"git -C {q(project)} rev-parse HEAD"),
            "subjects": subjects,
            "index_empty": True,
            "preexisting_dirty_paths_preserved": True,
            "dirty_path_count": len(dirty),
            "push_performed": False,
            "amend_performed": False,
            "reset_performed": False,
        }
        (GATE_ROOT / "preservation_repair.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(result, ensure_ascii=True, indent=2))
    finally:
        client.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("capture", "live-probe", "download-probe", "upload-stage", "stage-validate", "isolated-e2e", "status", "deploy", "regression", "verify", "formal-status", "finalize-services", "service-debug", "runtime-fix", "launcher-gate", "npm-audit-retry", "upload-reports", "commit", "git-audit", "preserve-dirty-repair"))
    args = parser.parse_args()
    if args.action == "capture":
        capture_action()
    elif args.action == "live-probe":
        live_probe_action()
    elif args.action == "download-probe":
        download_probe_action()
    elif args.action == "upload-stage":
        upload_stage_action()
    elif args.action == "stage-validate":
        stage_validate_action()
    elif args.action == "isolated-e2e":
        isolated_e2e_action()
    elif args.action == "status":
        status_action()
    elif args.action == "deploy":
        deploy_action()
    elif args.action == "regression":
        regression_action()
    elif args.action == "verify":
        verify_action()
    elif args.action == "formal-status":
        formal_status_action()
    elif args.action == "finalize-services":
        finalize_services_action()
    elif args.action == "service-debug":
        service_debug_action()
    elif args.action == "runtime-fix":
        runtime_fix_action()
    elif args.action == "launcher-gate":
        launcher_gate_action()
    elif args.action == "npm-audit-retry":
        npm_audit_retry_action()
    elif args.action == "upload-reports":
        upload_reports_action()
    elif args.action == "commit":
        commit_action()
    elif args.action == "git-audit":
        git_audit_action()
    elif args.action == "preserve-dirty-repair":
        preserve_dirty_repair_action()


if __name__ == "__main__":
    main()
