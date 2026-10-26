from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import secrets
import shlex
import stat
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import a100_p1_9 as p19


LOCAL_ROOT = Path(__file__).resolve().parents[1]
BASE_ROOT = LOCAL_ROOT / "remote_edit" / "p1_10r_base"
WORK_ROOT = LOCAL_ROOT / "remote_edit" / "p1_10r_work"
GATE_ROOT = LOCAL_ROOT / "remote_edit" / "p1_10r_gate"
REPORT_ROOT = LOCAL_ROOT / "reports" / "p1_10r_formal_preview_http500"
INDEX_ROOT = LOCAL_ROOT / "remote_edit" / "p1_10r_index"
ISOLATED_STATE = GATE_ROOT / "isolated_stage.json"
ISOLATED_RUNTIME_STATE = GATE_ROOT / "isolated_runtime.json"

EXPECTED_HEAD = "f85e4c7273581deaf0749851a5fb03cfd324d79c"

REMOTE_FILES = (
    "alembic.ini",
    "pyproject.toml",
    "migrations/env.py",
    "migrations/versions/0018_p1_10_preview_publication.py",
    "src/epidemic_core/discovery/review_app.py",
    "src/epidemic_core/discovery/review_main.py",
    "src/epidemic_core/discovery/candidate_pipeline.py",
    "src/epidemic_core/discovery/worker.py",
    "src/epidemic_core/discovery/static/review.p1-10-preview.css",
    "src/epidemic_core/discovery/static/review.p1-7ar3-v1.js",
    "src/epidemic_core/dashboard/app.py",
    "src/epidemic_core/dashboard/main.py",
    "src/epidemic_core/dashboard/static/dashboard.p1-10-preview.css",
    "src/epidemic_core/dashboard/static/dashboard.p1-10-preview.js",
    "src/epidemic_core/p1_10/__init__.py",
    "src/epidemic_core/p1_10/preview.py",
    "src/epidemic_core/p1_10/review_app.py",
    "src/epidemic_core/p1_10/dashboard_app.py",
    "scripts/source_review/_common.sh",
    "scripts/source_review/install_runtime.sh",
    "scripts/source_review/start_source_review.sh",
    "scripts/source_review/status_source_review.sh",
    "scripts/source_review/stop_source_review.sh",
    "scripts/dashboard/_common.sh",
    "scripts/dashboard/install_runtime.sh",
    "scripts/dashboard/start_dashboard.sh",
    "scripts/dashboard/status_dashboard.sh",
    "scripts/dashboard/stop_dashboard.sh",
    "scripts/preview_publication_worker/_common.sh",
    "scripts/preview_publication_worker/start_preview_publication_worker.sh",
    "scripts/preview_publication_worker/status_preview_publication_worker.sh",
    "scripts/preview_publication_worker/stop_preview_publication_worker.sh",
    "scripts/source_discovery_worker/_common.sh",
    "scripts/source_discovery_worker/start_source_discovery_worker.sh",
    "scripts/source_discovery_worker/status_source_discovery_worker.sh",
    "scripts/source_discovery_worker/stop_source_discovery_worker.sh",
    "tests/unit/test_p1_10_preview.py",
    "tests/unit/test_dashboard.py",
)

CHANGE_FILES = (
    "migrations/versions/0019_p1_10r_formal_preview_http500.py",
    "src/epidemic_core/p1_10/diagnostics.py",
    "src/epidemic_core/p1_10/dashboard_app.py",
    "src/epidemic_core/p1_10/review_app.py",
    "src/epidemic_core/discovery/review_app.py",
    "src/epidemic_core/dashboard/app.py",
    "src/epidemic_core/dashboard/static/dashboard.p1-10-preview.css",
    "src/epidemic_core/dashboard/static/dashboard.p1-10-preview.js",
    "scripts/source_review/_common.sh",
    "scripts/source_review/start_source_review.sh",
    "scripts/source_review/status_source_review.sh",
    "scripts/dashboard/_common.sh",
    "scripts/dashboard/start_dashboard.sh",
    "scripts/dashboard/status_dashboard.sh",
    "tests/unit/test_p1_10r_http500.py",
    "tests/unit/test_dashboard.py",
)

REPORT_FILES = (
    "P1_10R_FORMAL_PREVIEW_HTTP500_REPAIR_REPORT.md",
    "http500_root_causes.json",
    "failed_request_reconciliation.json",
    "deployment_version_alignment.json",
    "migration_results.json",
    "schema_permission_results.json",
    "preview_empty_state_results.json",
    "controlled_retrieval_results.json",
    "preview_publication_test_results.json",
    "dashboard_results.json",
    "browser_results.json",
    "trusted_immutability_results.json",
    "daily_training_isolation_results.json",
    "service_inventory.json",
    "security_regression_results.json",
    "git_submission_results.json",
    "USER_RETEST_GUIDE.md",
    "dashboard_preview_desktop_1440x900.png",
    "dashboard_preview_mobile_390x844.png",
    "review_console_desktop_1440x900.png",
    "review_console_mobile_390x844.png",
    "review_candidate_detail.png",
)

OVERLAP_FILES = (
    "scripts/dashboard/_common.sh",
    "scripts/dashboard/start_dashboard.sh",
)


def q(value: str) -> str:
    return shlex.quote(value)


def _remote_json(client: Any, python: str, setup: str, code: str, timeout: int = 600) -> Any:
    command = f"{setup}; exec {q(python)} -B -c {q(code)}"
    raw = p19.a100.checked(client, f"bash -c {q(command)}", timeout=timeout)
    return json.loads(raw)


def _status_path(line: str) -> str:
    if line.startswith("? "):
        return line[2:]
    if line.startswith("1 "):
        return line.split(" ", 8)[-1]
    if "\t" in line:
        return line.rsplit("\t", 1)[-1]
    return line.split()[-1]


def _safe_log_text(payload: str) -> str:
    value = payload
    value = re.sub(r"postgresql(?:\+\w+)?://[^\s'\"]+", "[DATABASE_URL_REDACTED]", value)
    value = re.sub(
        r"(?i)\b(authorization|cookie|password|token|secret|api[_-]?key)\b(\s*[=:]\s*)[^\s,;]+",
        r"\1\2[REDACTED]",
        value,
    )
    value = re.sub(r"(?i)(set-cookie:\s*)[^\r\n]+", r"\1[REDACTED]", value)
    return value


def capture_action() -> None:
    client, gate = p19.a100.connect()
    try:
        root, project = p19.a100.paths(client)
        head = p19.a100.checked(client, f"git -C {q(project)} rev-parse HEAD")
        if head != EXPECTED_HEAD:
            raise SystemExit("P1_10R_UNEXPECTED_HEAD")
        for directory in (BASE_ROOT, WORK_ROOT, GATE_ROOT, REPORT_ROOT):
            directory.mkdir(parents=True, exist_ok=True)
        status_text = p19.a100.checked(client, f"git -C {q(project)} status --porcelain=v2 -uall")
        staged = p19.a100.checked(client, f"git -C {q(project)} diff --cached --name-only")
        manifest: dict[str, Any] = {}
        sftp = client.open_sftp()
        try:
            for relative in REMOTE_FILES:
                remote_path = f"{project}/{relative}"
                try:
                    attrs = sftp.stat(remote_path)
                    with sftp.file(remote_path, "rb") as handle:
                        payload = handle.read()
                except OSError:
                    manifest[relative] = {"exists": False}
                    continue
                for target_root in (BASE_ROOT, WORK_ROOT):
                    target = target_root / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(payload)
                manifest[relative] = {
                    "exists": True,
                    "bytes": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "mode": stat.S_IMODE(attrs.st_mode),
                }
        finally:
            sftp.close()
        (GATE_ROOT / "status_before.txt").write_text(status_text, encoding="utf-8")
        (GATE_ROOT / "staged_before.txt").write_text(staged, encoding="utf-8")
        capture = {
            "captured_at_utc": datetime.now(timezone.utc).isoformat(),
            "branch": p19.a100.checked(client, f"git -C {q(project)} branch --show-current"),
            "head": head,
            "subject": p19.a100.checked(client, f"git -C {q(project)} show -s --format=%s HEAD"),
            "dirty_count": len(status_text.splitlines()),
            "dirty_paths": sorted(_status_path(line) for line in status_text.splitlines() if line),
            "staged_count": len(staged.splitlines()),
            "status_sha256": hashlib.sha256(status_text.encode()).hexdigest(),
            "files": manifest,
            "host_key_type": gate.get("key_type"),
            "host_key_fingerprint_recorded": bool(gate.get("fingerprint")),
            "secrets_exposed": False,
        }
        (GATE_ROOT / "capture.json").write_text(
            json.dumps(capture, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    "captured_at_utc": capture["captured_at_utc"],
                    "branch": capture["branch"],
                    "head": head,
                    "dirty_count": capture["dirty_count"],
                    "staged_count": capture["staged_count"],
                    "downloaded": sum(1 for item in manifest.values() if item.get("exists")),
                    "secrets_exposed": False,
                },
                indent=2,
            )
        )
    finally:
        client.close()


def scene_action() -> None:
    client, _gate = p19.a100.connect()
    try:
        root, project = p19.a100.paths(client)
        python = f"{root}/conda/envs/epidemic-core/bin/python"
        setup = (
            f"source {q(project + '/scripts/database/_env.sh')}; set -a; "
            f"source {q(project + '/.secrets/postgres.env')}; set +a"
        )
        code = r'''
import json,os,pathlib,re,subprocess
import sqlalchemy as sa
from psycopg import sql as psql
from sqlalchemy import text
from sqlalchemy.engine import make_url

url=make_url(os.environ["MIGRATION_DATABASE_URL"]).set(username="epidemicdb",password=os.environ["POSTGRES_ADMIN_PASSWORD"])
engine=sa.create_engine(url,hide_parameters=True)
trusted_tables=("data_sources","query_requests","retrieval_runs","source_fetches","raw_artifacts","normalized_observations","quality_assessments","dataset_snapshots","dataset_snapshot_observations")
preview_tables=("preview_review_decisions","preview_publication_jobs","preview_publication_events","preview_mapping_contracts","preview_staging_observations","preview_observations","preview_quality_assessments","preview_snapshots","preview_snapshot_observations","preview_dashboard_publications")
with engine.connect() as db:
 def exists(schema,name,kind="BASE TABLE"):
  return bool(db.execute(text("SELECT 1 FROM information_schema.tables WHERE table_schema=:s AND table_name=:n"),{"s":schema,"n":name}).scalar_one_or_none())
 trusted_counts={name:int(db.execute(text("SELECT count(*) FROM epidemic."+name)).scalar_one()) for name in trusted_tables}
 preview_counts={name:(int(db.execute(text("SELECT count(*) FROM epidemic_preview."+name)).scalar_one()) if exists("epidemic_preview",name) else None) for name in preview_tables}
 snapshots=[dict(r) for r in db.execute(text("SELECT id,row_count,content_sha256,created_at FROM epidemic.dataset_snapshots ORDER BY created_at,id")).mappings()]
 migration=db.execute(text("SELECT version_num FROM epidemic.alembic_version")).scalar_one()
 marker=[dict(r) for r in db.execute(text("SELECT environment,database_name,production,disposable FROM epidemic.environment_markers ORDER BY environment" )).mappings()] if exists("epidemic","environment_markers") else []
 preview_marker=[dict(r) for r in db.execute(text("SELECT environment,database_name,production,disposable FROM epidemic_preview.environment_markers ORDER BY environment")).mappings()] if exists("epidemic_preview","environment_markers") else []
 active_preview=[dict(r) for r in db.execute(text("SELECT id,publication_job_id,snapshot_id,observation_count,total_value,date_min,date_max,published_at FROM epidemic_preview.active_preview_publications_v1 ORDER BY published_at,id")).mappings()] if exists("epidemic_preview","preview_dashboard_publications") else []
 active_trusted=int(db.execute(text("SELECT count(*) FROM epidemic.dashboard_snapshot_catalog_v1")).scalar_one())
 recent_runs=[dict(r) for r in db.execute(text("SELECT discovery_run_id,country_iso3,disease_code,state,created_at,updated_at,candidate_count,last_error_code,last_event_code FROM epidemic_discovery.review_discovery_runs_v3 ORDER BY created_at DESC LIMIT 12")).mappings()]
 recent_preview_jobs=[dict(r) for r in db.execute(text("SELECT id,preview_decision_id,state,state_version,attempt_count,claim_owner,lease_expires_at,failure_code,snapshot_id,publication_id,created_at,updated_at FROM epidemic_preview.preview_publication_jobs ORDER BY created_at DESC LIMIT 12")).mappings()] if exists("epidemic_preview","preview_publication_jobs") else []
 recent_preview_decisions=[dict(r) for r in db.execute(text("SELECT id,review_case_id,candidate_id,candidate_version_id,discovery_run_id,reviewer_id,source_state_version,environment,test_only,created_at FROM epidemic_preview.preview_review_decisions ORDER BY created_at DESC LIMIT 12")).mappings()] if exists("epidemic_preview","preview_review_decisions") else []
 recent_preview_events=[dict(r) for r in db.execute(text("SELECT id,publication_job_id,from_state,to_state,event_code,requires_user_action,created_at FROM epidemic_preview.preview_publication_events ORDER BY created_at DESC,id DESC LIMIT 24")).mappings()] if exists("epidemic_preview","preview_publication_events") else []
 schedules=[dict(r) for r in db.execute(text("SELECT schedule_id,schedule_name,enabled,next_due_at,last_success_at,consecutive_failures,state_version FROM epidemic.daily_update_schedules ORDER BY schedule_name")).mappings()]
 schema_objects=[dict(r) for r in db.execute(text("SELECT table_name,table_type FROM information_schema.tables WHERE table_schema='epidemic_preview' ORDER BY table_type,table_name")).mappings()]
 required_roles=("epidemicreview","epidemic_dashboard_ro","epidemic_app","epidemicdb")
 role_search_paths=[]
 for role in required_roles:
  settings=db.execute(text("SELECT rolconfig FROM pg_roles WHERE rolname=:r"),{"r":role}).scalar_one_or_none()
  role_search_paths.append({"role":role,"settings":settings or []})
 privileges=[dict(r) for r in db.execute(text("SELECT grantee,table_name,privilege_type FROM information_schema.role_table_grants WHERE table_schema='epidemic_preview' ORDER BY grantee,table_name,privilege_type")).mappings()]
 schema_privileges=[]
 for role in required_roles:
  schema_privileges.append({"role":role,"usage":bool(db.execute(text("SELECT has_schema_privilege(:r,'epidemic_preview','USAGE')"),{"r":role}).scalar_one())})
 result={"migration":migration,"database_name":db.execute(text("SELECT current_database()")).scalar_one(),"marker":marker,"preview_marker":preview_marker,"trusted_counts":trusted_counts,"preview_counts":preview_counts,"snapshots":snapshots,"active_trusted_catalog_rows":active_trusted,"active_preview":active_preview,"recent_discovery_runs":recent_runs,"recent_preview_jobs":recent_preview_jobs,"recent_preview_decisions":recent_preview_decisions,"recent_preview_events":recent_preview_events,"daily_schedules":schedules,"training_rows":int(db.execute(text("SELECT count(*) FROM epidemic.training_snapshot_observations_v1")).scalar_one()),"schema_objects":schema_objects,"schema_privileges":schema_privileges,"table_privileges":privileges,"role_settings":role_search_paths}
print(json.dumps(result,default=str))
'''
        database = _remote_json(client, python, setup, code, timeout=600)

        runtime_code = r'''
import datetime,json,os,pathlib,re
project=pathlib.Path(os.environ["PROJECT"]);root=project.parent
files={
 "review":root/"epidemic-source-review-runtime/log/review.log",
 "dashboard":root/"epidemic-dashboard-runtime/log/dashboard.log",
 "preview_worker":project/"storage/logs/preview-publication-worker/worker.log",
 "discovery_worker":project/"storage/logs/source-discovery-worker/worker.log",
}
redactions=[
 (re.compile(r"postgresql(?:\+\w+)?://[^\s'\"]+",re.I),"[DATABASE_URL_REDACTED]"),
 (re.compile(r"(?i)\b(authorization|cookie|password|token|secret|api[_-]?key)\b(\s*[=:]\s*)[^\s,;]+"),r"\1\2[REDACTED]"),
 (re.compile(r"(?i)(set-cookie:\s*)[^\r\n]+"),r"\1[REDACTED]"),
]
logs={}
for name,path in files.items():
 if not path.is_file():
  logs[name]={"exists":False,"relative_path":str(path.relative_to(root))};continue
 lines=path.read_text(encoding="utf-8",errors="replace").splitlines()[-1600:]
 safe=[]
 for line in lines:
  for pattern,replacement in redactions:line=pattern.sub(replacement,line)
  safe.append(line)
 logs[name]={"exists":True,"relative_path":str(path.relative_to(root)),"size":path.stat().st_size,"mtime":datetime.datetime.fromtimestamp(path.stat().st_mtime,datetime.timezone.utc).isoformat(),"tail":safe}
pidfiles={"review":root/"epidemic-source-review-runtime/run/review.pid","dashboard":root/"epidemic-dashboard-runtime/run/dashboard.pid","preview_worker":project/"storage/run/preview-publication-worker/worker.pid","discovery_worker":project/"storage/run/source-discovery-worker/worker.pid"}
processes=[]
for name,path in pidfiles.items():
 try:pid=int(path.read_text().strip())
 except Exception:continue
 proc=pathlib.Path("/proc")/str(pid)
 if not proc.exists():continue
 stat=proc.stat()
 cmd=(proc/"cmdline").read_bytes().replace(b"\0",b" ").decode("utf-8","replace")
 processes.append({"service":name,"pid":pid,"uid":stat.st_uid,"cmd_sha256":__import__("hashlib").sha256(cmd.encode()).hexdigest(),"started_ticks":(proc/"stat").read_text().split()[21]})
print(json.dumps({"logs":logs,"processes":processes},ensure_ascii=False))
'''
        runtime = _remote_json(
            client,
            python,
            f"export PROJECT={q(project)}",
            runtime_code,
            timeout=300,
        )
        for name, item in runtime["logs"].items():
            if item.get("exists"):
                (GATE_ROOT / f"{name}_before.log").write_text(
                    _safe_log_text("\n".join(item.pop("tail")) + "\n"), encoding="utf-8"
                )
        git = {
            "branch": p19.a100.checked(client, f"git -C {q(project)} branch --show-current"),
            "head": p19.a100.checked(client, f"git -C {q(project)} rev-parse HEAD"),
            "subject": p19.a100.checked(client, f"git -C {q(project)} show -s --format=%s HEAD"),
            "dirty_count": len(
                p19.a100.checked(client, f"git -C {q(project)} status --porcelain=v2 -uall").splitlines()
            ),
            "staged_count": len(
                p19.a100.checked(client, f"git -C {q(project)} diff --cached --name-only").splitlines()
            ),
        }
        scene = {
            "captured_at_utc": datetime.now(timezone.utc).isoformat(),
            "git": git,
            "database": database,
            "runtime": runtime,
            "secrets_exposed": False,
        }
        GATE_ROOT.mkdir(parents=True, exist_ok=True)
        (GATE_ROOT / "production_scene.json").write_text(
            json.dumps(scene, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    "captured_at_utc": scene["captured_at_utc"],
                    "git": git,
                    "migration": database["migration"],
                    "trusted_counts": database["trusted_counts"],
                    "preview_counts": database["preview_counts"],
                    "recent_discovery_runs": database["recent_discovery_runs"][:3],
                    "recent_preview_jobs": database["recent_preview_jobs"],
                    "recent_preview_decisions": database["recent_preview_decisions"],
                    "active_preview_count": len(database["active_preview"]),
                    "training_rows": database["training_rows"],
                    "schema_privileges": database["schema_privileges"],
                    "runtime_processes": runtime["processes"],
                    "secrets_exposed": False,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        client.close()


def stage_action() -> None:
    client, _gate = p19.a100.connect()
    try:
        _root, project = p19.a100.paths(client)
        head = p19.a100.checked(client, f"git -C {q(project)} rev-parse HEAD")
        if head != EXPECTED_HEAD:
            raise SystemExit("P1_10R_UNEXPECTED_HEAD")
        stage = f"{project}/storage/p1_10r/stage_{secrets.token_hex(6)}"
        p19.a100.checked(client, f"mkdir -p {q(stage)} && chmod 700 {q(stage)}")
        archive = f"git -C {q(project)} archive HEAD | tar -x -C {q(stage)}"
        p19.a100.checked(client, f"bash -c {q(archive)}", timeout=300)
        sftp = client.open_sftp()
        uploaded: list[str] = []
        try:
            for relative in CHANGE_FILES:
                source = WORK_ROOT / relative
                if not source.is_file():
                    raise FileNotFoundError(relative)
                remote_path = f"{stage}/{relative}"
                p19.a100.checked(client, f"mkdir -p {q(remote_path.rsplit('/', 1)[0])}")
                with sftp.file(remote_path, "wb") as handle:
                    handle.write(source.read_bytes())
                sftp.chmod(remote_path, 0o755 if relative.endswith(".sh") else 0o644)
                uploaded.append(relative)
        finally:
            sftp.close()
        state = {
            "stage": stage,
            "head": head,
            "uploaded": uploaded,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        GATE_ROOT.mkdir(parents=True, exist_ok=True)
        ISOLATED_STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")
        print(
            json.dumps(
                {
                    "stage_created": True,
                    "head": head,
                    "uploaded_count": len(uploaded),
                    "production_worktree_modified": False,
                    "secrets_exposed": False,
                },
                indent=2,
            )
        )
    finally:
        client.close()


def _load_stage() -> dict[str, Any]:
    if not ISOLATED_STATE.is_file():
        raise SystemExit("P1_10R_ISOLATED_STAGE_MISSING")
    return json.loads(ISOLATED_STATE.read_text(encoding="utf-8"))


def stage_tests_action() -> None:
    state = _load_stage()
    stage = str(state["stage"])
    client, _gate = p19.a100.connect()
    try:
        root, project = p19.a100.paths(client)
        if not stage.startswith(project + "/storage/p1_10r/stage_"):
            raise SystemExit("P1_10R_UNSAFE_STAGE")
        python = f"{root}/conda/envs/epidemic-core/bin/python"
        commands = (
            (
                "focused_pytest",
                f"cd {q(stage)} && PYTHONPATH={q(stage + '/src')} {q(python)} -m pytest -q tests/unit/test_p1_10r_http500.py tests/unit/test_p1_10_preview.py",
                1200,
            ),
            (
                "ruff",
                f"cd {q(stage)} && {q(python)} -m ruff check "
                + " ".join(q(item) for item in CHANGE_FILES if item.endswith(".py")),
                600,
            ),
            (
                "format",
                f"cd {q(stage)} && {q(python)} -m ruff format --check "
                + " ".join(q(item) for item in CHANGE_FILES if item.endswith(".py")),
                600,
            ),
            (
                "bash_syntax",
                "bash -n " + " ".join(q(f"{stage}/{item}") for item in CHANGE_FILES if item.endswith(".sh")),
                300,
            ),
            (
                "javascript_syntax",
                f"{q(project + '/runtime/node-v24.18.0-linux-x64/bin/node')} --check "
                f"{q(stage + '/src/epidemic_core/dashboard/static/dashboard.p1-10-preview.js')}",
                300,
            ),
        )
        results = []
        failed = False
        for label, command, timeout in commands:
            rc, out, err = p19.a100.run(client, f"bash -lc {q(command)}", timeout=timeout)
            safe = _safe_log_text(out + "\n" + err).replace(stage, "[isolated-project]").replace(project, "[project]")
            results.append(
                {
                    "label": label,
                    "returncode": rc,
                    "output_sha256": hashlib.sha256(safe.encode()).hexdigest(),
                    "tail": safe.splitlines()[-20:],
                }
            )
            failed = failed or rc != 0
        output = {"pass": not failed, "results": results, "production_worktree_modified": False}
        (GATE_ROOT / "isolated_static_tests.json").write_text(
            json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(output, ensure_ascii=False, indent=2))
        if failed:
            raise SystemExit(1)
    finally:
        client.close()


def stage_format_action() -> None:
    state = _load_stage()
    stage = str(state["stage"])
    client, _gate = p19.a100.connect()
    try:
        root, project = p19.a100.paths(client)
        if not stage.startswith(project + "/storage/p1_10r/stage_"):
            raise SystemExit("P1_10R_UNSAFE_STAGE")
        python = f"{root}/conda/envs/epidemic-core/bin/python"
        python_files = [item for item in CHANGE_FILES if item.endswith(".py")]
        sftp = client.open_sftp()
        try:
            for relative in python_files:
                source = WORK_ROOT / relative
                if not source.is_file():
                    raise SystemExit(f"P1_10R_LOCAL_FILE_MISSING:{relative}")
                sftp.put(str(source), f"{stage}/{relative}")
        finally:
            sftp.close()
        command = f"cd {q(stage)} && {q(python)} -m ruff format " + " ".join(q(item) for item in python_files)
        p19.a100.checked(client, f"bash -lc {q(command)}", timeout=600)
        sftp = client.open_sftp()
        try:
            for relative in python_files:
                target = WORK_ROOT / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                with sftp.file(f"{stage}/{relative}", "rb") as handle:
                    target.write_bytes(handle.read())
        finally:
            sftp.close()
        print(json.dumps({"formatted_and_synced": len(python_files), "production_worktree_modified": False}))
    finally:
        client.close()


def full_regression_action() -> None:
    state = _load_stage()
    stage = str(state["stage"])
    client, _gate = p19.a100.connect()
    try:
        root, project = p19.a100.paths(client)
        if not stage.startswith(project + "/storage/p1_10r/stage_"):
            raise SystemExit("P1_10R_UNSAFE_STAGE")
        python = f"{root}/conda/envs/epidemic-core/bin/python"
        code = r'''import hashlib,json,os,pathlib,re,secrets,subprocess,time
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import make_url

project=pathlib.Path(os.environ["STAGE"]);python=os.environ["PYTHON_BIN"]
formal=make_url(os.environ["MIGRATION_DATABASE_URL"]).set(username="epidemicdb",password=os.environ["POSTGRES_ADMIN_PASSWORD"])
app=make_url(os.environ["APP_DATABASE_URL"]);cluster=formal.set(database="postgres")
name="epidemic_p1_10r_pytest_"+secrets.token_hex(5);env=os.environ.copy()
def run(args,timeout=1800,run_env=None):return subprocess.run(args,cwd=project,env=run_env or env,text=True,capture_output=True,timeout=timeout)
started=time.monotonic();admin=sa.create_engine(cluster,isolation_level="AUTOCOMMIT",hide_parameters=True)
with admin.connect() as db:db.exec_driver_sql('CREATE DATABASE "'+name+'" TEMPLATE template0 ENCODING \'UTF8\'')
try:
 test_env=os.environ.copy();test_env["MIGRATION_DATABASE_URL"]=formal.set(database=name).render_as_string(hide_password=False);test_env["APP_DATABASE_URL"]=app.set(database=name).render_as_string(hide_password=False);test_env["PYTHONPATH"]="src"
 test_engine=sa.create_engine(formal.set(database=name),hide_parameters=True)
 with test_engine.begin() as db:
  db.exec_driver_sql("CREATE SCHEMA IF NOT EXISTS epidemic")
  db.exec_driver_sql("GRANT USAGE ON SCHEMA epidemic TO epidemic_app")
 test_engine.dispose()
 migrate=run([python,"-m","alembic","upgrade","head"],900,test_env)
 if migrate.returncode:raise RuntimeError("PYTEST_MIGRATION_FAILED:"+hashlib.sha256(migrate.stderr.encode()).hexdigest())
 seed=run([python,"-m","epidemic_core.cli","seed-reference"],300,test_env)
 if seed.returncode:raise RuntimeError("PYTEST_SEED_FAILED:"+hashlib.sha256(seed.stderr.encode()).hexdigest())
 result=run([python,"-m","pytest","-p","no:cacheprovider","-q"],3600,test_env)
 safe=(result.stdout+"\n"+result.stderr).replace(str(project),"[isolated-project]").replace(os.environ["DATA_ROOT"],"[data-root]")
 safe=re.sub(r"postgresql(?:\+\w+)?://[^\s]+","[DATABASE_URL_REDACTED]",safe)
 print(json.dumps({"label":"pytest_full_isolated","returncode":result.returncode,"duration_seconds":round(time.monotonic()-started,3),"output_sha256":hashlib.sha256(safe.encode()).hexdigest(),"tail":safe.splitlines()[-40:],"database_disposable":True,"database_initialized_from_migrations":True,"reference_seeded":True,"formal_database_written":False}))
finally:
 with admin.connect() as db:
  db.execute(text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=:name"),{"name":name});db.exec_driver_sql('DROP DATABASE IF EXISTS "'+name+'"')
 admin.dispose()
'''
        setup = (
            f"source {q(project + '/scripts/database/_env.sh')}; set -a; "
            f"source {q(project + '/.secrets/postgres.env')}; set +a; "
            f"export STAGE={q(stage)} PYTHON_BIN={q(python)} DATA_ROOT={q(root)}"
        )
        pytest_result = _remote_json(client, python, setup, code, timeout=5400)

        def check(label: str, command: str, timeout: int) -> dict[str, Any]:
            started = time.monotonic()
            rc, out, err = p19.a100.run(client, f"bash -lc {q(command)}", timeout=timeout)
            safe = _safe_log_text(out + "\n" + err).replace(stage, "[isolated-project]").replace(
                project, "[project]"
            )
            safe = re.sub(r"https?://\S+", "[URL_REDACTED]", safe)
            return {
                "label": label,
                "returncode": rc,
                "duration_seconds": round(time.monotonic() - started, 3),
                "output_sha256": hashlib.sha256(safe.encode()).hexdigest(),
                "tail": safe.splitlines()[-30:],
            }

        node_bin = f"{project}/runtime/node-v24.18.0-linux-x64/bin"
        npm = f"{node_bin}/npm"
        npm_dir = f"{stage}/integrations/openclaw/epidemic-query-tools"
        formal_modules = f"{project}/integrations/openclaw/epidemic-query-tools/node_modules"
        p19.a100.checked(
            client,
            f"test -d {q(formal_modules)} && test -d {q(npm_dir)} && "
            f"if test ! -e {q(npm_dir + '/node_modules')}; then ln -s {q(formal_modules)} {q(npm_dir + '/node_modules')}; fi",
            timeout=60,
        )
        npm_prefix = f"export PATH={q(node_bin)}:$PATH; cd {q(npm_dir)}"
        cli = f"{project}/scripts/openclaw/openclaw_cli.sh"
        shell_files = " ".join(q(f"{stage}/{item}") for item in CHANGE_FILES if item.endswith(".sh"))
        checks = [
            pytest_result,
            check("ruff_check", f"cd {q(stage)}; {q(python)} -m ruff check src tests migrations", 1200),
            check("ruff_format", f"cd {q(stage)}; {q(python)} -m ruff format --check src tests migrations", 1200),
            check("mypy_strict", f"cd {q(stage)}; {q(python)} -m mypy --strict src", 1800),
            check("pip_check", f"{q(python)} -m pip check", 600),
            check("bash_syntax", f"bash -n {shell_files}", 300),
            check("alembic_single_head", f"cd {q(stage)}; {q(python)} -m alembic -c alembic.ini heads", 600),
            check("npm_build", f"{npm_prefix}; {q(npm)} run build", 1200),
            check("npm_test", f"{npm_prefix}; {q(npm)} test", 1200),
            check(
                "npm_audit",
                f"{npm_prefix}; ({q(stage + '/scripts/download/with_autodl_turbo.sh')} "
                f"{q(npm)} audit --json --registry=https://registry.npmjs.org "
                "--fetch-retries=1 --fetch-timeout=60000 || "
                f"(unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; {q(npm)} "
                "audit --json --registry=https://registry.npmjs.org --fetch-retries=1 --fetch-timeout=60000))",
                1200,
            ),
            check("openclaw_doctor_lint", f"{q(cli)} doctor --lint --json", 600),
            check("openclaw_doctor_deep", f"{q(cli)} doctor --deep --non-interactive", 900),
            check("openclaw_security_deep", f"{q(cli)} security audit --deep --json", 900),
        ]
        required = {
            "pytest_full_isolated",
            "ruff_check",
            "ruff_format",
            "mypy_strict",
            "pip_check",
            "bash_syntax",
            "alembic_single_head",
            "npm_build",
            "npm_test",
            "npm_audit",
        }
        result = {
            "pass": all(row["returncode"] == 0 for row in checks if row["label"] in required),
            "checks": checks,
            "openclaw_checks_executed": True,
            "production_worktree_modified": False,
        }
        (GATE_ROOT / "full_regression_results.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
        print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
        if not result["pass"]:
            raise SystemExit("P1_10R_FULL_REGRESSION_FAILED")
    finally:
        client.close()


def migration19_test_action() -> None:
    state = _load_stage()
    stage = str(state["stage"])
    client, _gate = p19.a100.connect()
    try:
        root, project = p19.a100.paths(client)
        if not stage.startswith(project + "/storage/p1_10r/stage_"):
            raise SystemExit("P1_10R_UNSAFE_STAGE")
        python = f"{root}/conda/envs/epidemic-core/bin/python"
        code = r'''import hashlib,json,os,pathlib,secrets,subprocess
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import make_url

project=pathlib.Path(os.environ["PROJECT"])
stage=pathlib.Path(os.environ["STAGE"])
python=os.environ["PYTHON_BIN"]
pgbin=pathlib.Path(os.environ["POSTGRES_BIN"])
formal=make_url(os.environ["MIGRATION_DATABASE_URL"]).set(username="epidemicdb",password=os.environ["POSTGRES_ADMIN_PASSWORD"])
cluster=formal.set(database="postgres")
dumps=sorted((project/"storage/backups/p1_10").glob("*/pre_migration.dump"),key=lambda p:p.stat().st_mtime,reverse=True)
if not dumps:raise RuntimeError("P1_10R_BACKUP_MISSING")
dump=dumps[0]
if not dump.is_file() or dump.stat().st_size<1024:raise RuntimeError("P1_10R_BACKUP_INVALID")
dump_sha=hashlib.sha256(dump.read_bytes()).hexdigest()
name="epidemic_p1_10r_restore_"+secrets.token_hex(5)
checks=[]
env=os.environ.copy();env["PGPASSWORD"]=formal.password or ""
def run(label,args,timeout=1800,extra=None):
 e=env.copy();e.update(extra or {})
 proc=subprocess.run(args,cwd=stage,env=e,text=True,capture_output=True,timeout=timeout)
 safe=(proc.stdout+"\n"+proc.stderr).replace(str(project),"[project]").replace(str(stage),"[isolated-project]")
 checks.append({"label":label,"returncode":proc.returncode,"output_sha256":hashlib.sha256(safe.encode()).hexdigest(),"tail":safe.splitlines()[-12:]})
 if proc.returncode:raise RuntimeError(label+" failed output_sha256="+hashlib.sha256(safe.encode()).hexdigest())
def pgargs(database):return ["-h",formal.host or "127.0.0.1","-p",str(formal.port or 5432),"-U",formal.username or "epidemicdb","-d",database]
cluster_engine=sa.create_engine(cluster,isolation_level="AUTOCOMMIT",hide_parameters=True)
with cluster_engine.connect() as db:db.exec_driver_sql('CREATE DATABASE "'+name+'" TEMPLATE template0 ENCODING \'UTF8\'')
try:
 run("restore",[str(pgbin/"pg_restore"),*pgargs(name),"--exit-on-error","--no-owner","--no-privileges",str(dump)])
 test_url=formal.set(database=name);test_engine=sa.create_engine(test_url,hide_parameters=True)
 def inventory(engine):
  with engine.connect() as db:
   names=("data_sources","query_requests","retrieval_runs","source_fetches","raw_artifacts","normalized_observations","quality_assessments","dataset_snapshots","dataset_snapshot_observations")
   return {"counts":{n:int(db.execute(text('SELECT count(*) FROM epidemic.'+n)).scalar_one()) for n in names},"snapshots":[dict(r) for r in db.execute(text("SELECT id,row_count,content_sha256 FROM epidemic.dataset_snapshots ORDER BY created_at,id")).mappings()]}
 source=inventory(sa.create_engine(formal,hide_parameters=True));restored=inventory(test_engine)
 if source!=restored:raise RuntimeError("P1_10R_RESTORE_INVENTORY_MISMATCH")
 with test_engine.begin() as db:
  db.exec_driver_sql("GRANT USAGE ON SCHEMA epidemic_preview TO epidemicreview,epidemic_dashboard_ro")
 migration_env=env.copy();migration_env["MIGRATION_DATABASE_URL"]=test_url.render_as_string(hide_password=False);migration_env["PYTHONPATH"]=str(stage/"src")
 run("upgrade_0019",[python,"-m","alembic","upgrade","head"],900,migration_env)
 with test_engine.connect() as db:
  revision=db.execute(text("SELECT version_num FROM epidemic.alembic_version")).scalar_one()
  safe_views={name:bool(db.execute(text("SELECT to_regclass(:name) IS NOT NULL"),{"name":"epidemic_preview."+name}).scalar_one()) for name in ("preview_safe_source_columns_v1","preview_discovery_candidate_links_v1","runtime_readiness_v1")}
  db.rollback()
  positive={}
  for label,role,sql in (
   ("review_safe_columns","epidemicreview","SELECT count(*) FROM epidemic_preview.preview_safe_source_columns_v1"),
   ("review_safe_links","epidemicreview","SELECT count(*) FROM epidemic_preview.preview_discovery_candidate_links_v1"),
   ("review_readiness","epidemicreview","SELECT count(*) FROM epidemic_preview.runtime_readiness_v1"),
   ("dashboard_readiness","epidemic_dashboard_ro","SELECT count(*) FROM epidemic_preview.runtime_readiness_v1"),
  ):
   tx=db.begin()
   try:db.execute(text('SET LOCAL ROLE "'+role+'"'));db.execute(text(sql));positive[label]=True
   except Exception:positive[label]=False
   finally:tx.rollback()
  negative={}
  for label,role,sql in (
   ("review_raw_schema_columns_denied","epidemicreview","SELECT count(*) FROM epidemic_discovery.source_schema_columns"),
   ("dashboard_quarantine_denied","epidemic_dashboard_ro","SELECT count(*) FROM epidemic_discovery.quarantine_artifacts"),
   ("dashboard_preview_write_denied","epidemic_dashboard_ro","DELETE FROM epidemic_preview.preview_observations"),
   ("daily_preview_read_denied","epidemic_app","SELECT count(*) FROM epidemic_preview.preview_observations"),
  ):
   tx=db.begin()
   try:db.execute(text('SET LOCAL ROLE "'+role+'"'));db.execute(text(sql));negative[label]=False
   except Exception:negative[label]=True
   finally:tx.rollback()
 run("downgrade_0018",[python,"-m","alembic","downgrade","0018_p1_10_preview_publication"],900,migration_env)
 with test_engine.connect() as db:
  downgraded={"revision":db.execute(text("SELECT version_num FROM epidemic.alembic_version")).scalar_one(),"new_views_absent":all(db.execute(text("SELECT to_regclass(:name) IS NULL"),{"name":"epidemic_preview."+n}).scalar_one() for n in safe_views)}
 run("reupgrade_0019",[python,"-m","alembic","upgrade","head"],900,migration_env)
 run("single_head",[python,"-m","alembic","heads"],300,migration_env)
 with test_engine.connect() as db:final_revision=db.execute(text("SELECT version_num FROM epidemic.alembic_version")).scalar_one()
 final_inventory=inventory(test_engine);test_engine.dispose()
 passed=revision=="0019_p1_10r_formal_preview_http500" and all(safe_views.values()) and all(positive.values()) and all(negative.values()) and downgraded=={"revision":"0018_p1_10_preview_publication","new_views_absent":True} and final_revision==revision and final_inventory==source
 if not passed:raise RuntimeError("P1_10R_MIGRATION_GATE_FAILED")
 print(json.dumps({"pass":True,"backup":{"sha256":dump_sha,"bytes":dump.stat().st_size,"path":"[project]/storage/backups/p1_10/<redacted>/pre_migration.dump"},"restored_inventory_match":True,"trusted_inventory":source,"revision":revision,"safe_views":safe_views,"positive_permissions":positive,"negative_permissions":negative,"downgraded":downgraded,"reupgraded_revision":final_revision,"checks":checks,"formal_database_modified":False},default=str))
finally:
 with cluster_engine.connect() as db:
  db.execute(text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=:name"),{"name":name});db.exec_driver_sql('DROP DATABASE IF EXISTS "'+name+'"')
 cluster_engine.dispose()
'''
        setup = (
            f"source {q(project + '/scripts/database/_env.sh')}; set -a; "
            f"source {q(project + '/.secrets/postgres.env')}; set +a; "
            f"export PROJECT={q(project)} STAGE={q(stage)} PYTHON_BIN={q(python)} "
            f"POSTGRES_BIN={q(root + '/conda/envs/postgresql18/bin')}"
        )
        result = _remote_json(client, python, setup, code, timeout=5400)
        (GATE_ROOT / "migration19_results.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    finally:
        client.close()


def isolated_e2e_action() -> None:
    state = _load_stage()
    stage = str(state["stage"])
    client, _gate = p19.a100.connect()
    try:
        root, project = p19.a100.paths(client)
        if not stage.startswith(project + "/storage/p1_10r/stage_"):
            raise SystemExit("P1_10R_UNSAFE_STAGE")
        python = f"{root}/conda/envs/epidemic-core/bin/python"
        code = r'''import datetime as dt,hashlib,json,os,pathlib,secrets,signal,subprocess,time,urllib.request,uuid
import sqlalchemy as sa
from psycopg import sql as psql
from sqlalchemy import text
from sqlalchemy.engine import make_url

from epidemic_core.p1_10.preview import PreviewGate,canonical_json,process_one

project=pathlib.Path(os.environ["PROJECT"])
stage=pathlib.Path(os.environ["STAGE"])
python=os.environ["PYTHON_BIN"]
pgbin=pathlib.Path(os.environ["POSTGRES_BIN"])
source_admin=make_url(os.environ["DEMO_ADMIN_DATABASE_URL"])
cluster_admin=source_admin.set(database="postgres")
name="epidemic_p1_10r_e2e_"+secrets.token_hex(5)
dump_root=stage/"storage"/"isolated-e2e"
dump_root.mkdir(parents=True,exist_ok=True,mode=0o700)
dump=dump_root/"source.dump"
logs=dump_root/"logs";logs.mkdir(parents=True,exist_ok=True,mode=0o700)
env=os.environ.copy();env["PGPASSWORD"]=source_admin.password or ""
checks=[]
def safe_output(value):return value.replace(str(project),"[project]").replace(str(stage),"[isolated-project]")
def run(label,args,timeout=1800,extra=None):
 e=env.copy();e.update(extra or {})
 p=subprocess.run(args,cwd=stage,env=e,text=True,capture_output=True,timeout=timeout)
 safe=safe_output(p.stdout+"\n"+p.stderr)
 checks.append({"label":label,"returncode":p.returncode,"output_sha256":hashlib.sha256(safe.encode()).hexdigest(),"tail":safe.splitlines()[-12:]})
 if p.returncode:raise RuntimeError(label+" failed output_sha256="+hashlib.sha256(safe.encode()).hexdigest())
def pgargs(url):return ["-h",url.host or "127.0.0.1","-p",str(url.port or 5432),"-U",url.username or "p1_9_demo_admin","-d",url.database]
run("demo_clone_dump",[str(pgbin/"pg_dump"),*pgargs(source_admin),"--format=custom","--file",str(dump)])
dump_sha=hashlib.sha256(dump.read_bytes()).hexdigest()
cluster_engine=sa.create_engine(cluster_admin,isolation_level="AUTOCOMMIT",hide_parameters=True)
with cluster_engine.connect() as db:db.exec_driver_sql('CREATE DATABASE "'+name+'" TEMPLATE template0 ENCODING \'UTF8\'')
clone_admin=source_admin.set(database=name)
try:
 run("demo_clone_restore",[str(pgbin/"pg_restore"),*pgargs(clone_admin),"--exit-on-error",str(dump)])
 migration_env=env.copy();migration_env.update({"MIGRATION_DATABASE_URL":clone_admin.render_as_string(hide_password=False),"PYTHONPATH":str(stage/"src")})
 run("clone_upgrade_0019",[python,"-m","alembic","upgrade","head"],900,migration_env)
 admin=sa.create_engine(clone_admin,hide_parameters=True,pool_pre_ping=True)
 source_app=make_url(os.environ["DEMO_APP_DATABASE_URL"]);app_url=source_app.set(database=name)
 review_password=secrets.token_urlsafe(36);dashboard_password=secrets.token_urlsafe(36);preview_password=secrets.token_urlsafe(36)
 role_passwords={"epidemicreview":review_password,"epidemic_dashboard_ro":dashboard_password,"epidemicpreview":preview_password}
 with admin.begin() as db:
  db.execute(text("UPDATE epidemic_preview.environment_markers SET environment='p1_10_test',database_name=current_database(),production=false,disposable=true"))
  preview_tables=("preview_publication_events","preview_staging_observations","preview_quality_assessments","preview_snapshot_observations","preview_dashboard_publications","preview_snapshots","preview_observations","preview_mapping_contracts","preview_publication_jobs","preview_review_decisions")
  db.exec_driver_sql("TRUNCATE "+",".join("epidemic_preview."+n for n in preview_tables)+" RESTART IDENTITY CASCADE")
  for role,password in role_passwords.items():
   if not db.execute(text("SELECT 1 FROM pg_roles WHERE rolname=:role"),{"role":role}).scalar_one_or_none():
    db.exec_driver_sql("CREATE ROLE "+role+" NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT")
   db.connection.driver_connection.execute(psql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}").format(psql.Identifier(role),psql.Literal(password)))
   db.exec_driver_sql('GRANT CONNECT ON DATABASE "'+name+'" TO '+role)
  db.exec_driver_sql("GRANT USAGE ON SCHEMA epidemic_discovery,epidemic_preview TO epidemicreview")
  db.exec_driver_sql("GRANT SELECT ON ALL TABLES IN SCHEMA epidemic_discovery TO epidemicreview")
  db.exec_driver_sql("REVOKE SELECT ON epidemic_discovery.candidate_fetches FROM epidemicreview")
  db.exec_driver_sql("GRANT SELECT ON epidemic_preview.preview_review_cards_v1,epidemic_preview.preview_review_decisions,epidemic_preview.preview_publication_jobs,epidemic_preview.preview_publication_events,epidemic_preview.preview_safe_source_columns_v1,epidemic_preview.preview_discovery_candidate_links_v1,epidemic_preview.preview_candidate_version_history_v1,epidemic_preview.preview_candidate_dataset_summary_v1,epidemic_preview.preview_candidate_fetches_safe_v1,epidemic_preview.runtime_readiness_v1 TO epidemicreview")
  db.exec_driver_sql("GRANT EXECUTE ON FUNCTION epidemic_preview.submit_preview_decision(uuid,text,text,uuid,integer,text,jsonb,jsonb,char,jsonb,char,text,boolean),epidemic_preview.queue_preview_publication(uuid,uuid),epidemic_preview.revoke_preview_publication(uuid,text,text,uuid) TO epidemicreview")
  db.exec_driver_sql("GRANT USAGE ON SCHEMA epidemic,epidemic_preview TO epidemic_dashboard_ro")
  db.exec_driver_sql("GRANT SELECT ON ALL TABLES IN SCHEMA epidemic TO epidemic_dashboard_ro")
  db.exec_driver_sql("GRANT SELECT ON epidemic_preview.active_preview_publications_v1,epidemic_preview.preview_dashboard_series_v1,epidemic_preview.preview_lineage_v1,epidemic_preview.runtime_readiness_v1 TO epidemic_dashboard_ro")
  db.exec_driver_sql("GRANT USAGE ON SCHEMA epidemic_discovery,epidemic_preview,epidemic TO epidemicpreview")
  db.exec_driver_sql("GRANT SELECT ON epidemic.alembic_version TO epidemicpreview")
  db.exec_driver_sql("GRANT SELECT ON epidemic_discovery.source_review_cases,epidemic_discovery.source_candidates,epidemic_discovery.source_candidate_versions,epidemic_discovery.quarantine_artifacts,epidemic_discovery.source_schema_profiles,epidemic_discovery.source_mapping_proposals,epidemic_discovery.source_quality_assessments,epidemic_discovery.discovery_runs,epidemic_discovery.discovery_run_candidate_links TO epidemicpreview")
  db.exec_driver_sql("GRANT SELECT,INSERT,UPDATE ON ALL TABLES IN SCHEMA epidemic_preview TO epidemicpreview")
  db.exec_driver_sql("GRANT DELETE ON epidemic_preview.preview_staging_observations TO epidemicpreview")
  db.exec_driver_sql("GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA epidemic_preview TO epidemicpreview")
  db.exec_driver_sql('GRANT CONNECT ON DATABASE "'+name+'" TO p1_9_demo_app')
 def role_url(role,password):return clone_admin.set(username=role,password=password).render_as_string(hide_password=False)
 review_url=role_url("epidemicreview",review_password);dashboard_url=role_url("epidemic_dashboard_ro",dashboard_password);preview_url=role_url("epidemicpreview",preview_password)
 trusted_names=("data_sources","query_requests","retrieval_runs","source_fetches","raw_artifacts","normalized_observations","quality_assessments","dataset_snapshots","dataset_snapshot_observations")
 def trusted(engine):
  with engine.connect() as db:return {"counts":{n:int(db.execute(text('SELECT count(*) FROM epidemic.'+n)).scalar_one()) for n in trusted_names},"snapshots":[dict(r) for r in db.execute(text("SELECT id,row_count,content_sha256 FROM epidemic.dataset_snapshots ORDER BY created_at,id")).mappings()]}
 before=trusted(admin)
 with admin.begin() as db:
  revision=db.execute(text("SELECT version_num FROM epidemic.alembic_version")).scalar_one()
  disease=db.execute(text("SELECT id FROM epidemic.diseases WHERE canonical_name ILIKE '%Dengue%' ORDER BY id LIMIT 1")).scalar_one()
  request={"country_iso3":"BRA","disease_id":str(disease),"desired_metrics":["cases_new"],"time_window_kind":"latest_rolling_year","preferred_source_tiers":["NATIONAL_HEALTH","GOVERNMENT_OPEN_DATA","REGIONAL_HEALTH","INTERNATIONAL_PUBLIC_HEALTH","PUBLIC_HEALTH_RESEARCH"],"language_preferences":["pt","en"],"max_candidates":5,"retry_nonce":str(uuid.uuid4())}
  receipt=dict(db.execute(text("SELECT * FROM epidemic_discovery.submit_keyfree_discovery_v2(CAST(:request AS jsonb))"),{"request":json.dumps(request,separators=(",",":"))}).mappings().one())
 run_id=receipt["run_id"];submitted=time.monotonic()
 worker_env=os.environ.copy();worker_env.update({"APP_DATABASE_URL":app_url.render_as_string(hide_password=False),"PYTHONPATH":str(stage/"src"),"DISCOVERY_QUARANTINE_ROOT":str(project/"storage/p1_9_demo/quarantine/sha256"),"DISCOVERY_DIRECTORY_REGISTRY":str(stage/"config/source_discovery/directories.v1.json"),"DISCOVERY_EXTRACTOR_PATH":str(project.parent/"epidemic-extractor-runtime/extractor.py"),"DISCOVERY_EXTRACTOR_PYTHON":"/usr/bin/python3","DISCOVERY_DUCKDUCKGO_ENABLED":"0","DISCOVERY_PARALLEL_FREE_ENABLED":"0","DISCOVERY_BRAVE_ENABLED":"0"})
 for key in ("BRAVE_SEARCH_API_KEY","BRAVE_API_KEY","http_proxy","https_proxy","HTTP_PROXY","HTTPS_PROXY","all_proxy","ALL_PROXY"):worker_env.pop(key,None)
 worker_log=open(logs/"discovery-worker.log","ab",buffering=0)
 worker=subprocess.Popen([python,"-m","epidemic_core.discovery.worker"],cwd=stage,env=worker_env,stdout=worker_log,stderr=subprocess.STDOUT,start_new_session=True)
 claim_delay_ms=None
 try:
  deadline=submitted+10
  while time.monotonic()<deadline:
   with admin.connect() as db:
    delta=db.execute(text("SELECT e.created_at-r.created_at FROM epidemic_discovery.discovery_run_events e JOIN epidemic_discovery.discovery_runs r ON r.id=e.discovery_run_id WHERE e.discovery_run_id=:run AND e.event_code='WORKER_CLAIMED' ORDER BY e.created_at,e.id LIMIT 1"),{"run":run_id}).scalar_one_or_none()
    if delta is not None:claim_delay_ms=max(0,int(delta.total_seconds()*1000));break
   time.sleep(.2)
  if claim_delay_ms is None:raise RuntimeError("P1_10R_DISCOVERY_CLAIM_TIMEOUT")
  terminal={"REVIEW_READY","DISCOVERY_COMPLETE_NO_CANDIDATE","DISCOVERY_PARTIAL_FREE_PROVIDER_UNAVAILABLE","PROVIDER_DEGRADED","FAILED_TERMINAL","INVALID_REQUEST"};deadline=time.monotonic()+840
  while True:
   with admin.connect() as db:run_row=dict(db.execute(text("SELECT state,terminal_reason_code,created_at,completed_at,progress_json FROM epidemic_discovery.discovery_runs WHERE id=:run"),{"run":run_id}).mappings().one())
   if run_row["state"] in terminal:break
   if time.monotonic()>deadline:raise RuntimeError("P1_10R_DISCOVERY_TERMINAL_TIMEOUT")
   time.sleep(1)
 finally:
  try:os.killpg(worker.pid,signal.SIGTERM)
  except ProcessLookupError:pass
  try:worker.wait(timeout=15)
  except subprocess.TimeoutExpired:
   try:os.killpg(worker.pid,signal.SIGKILL)
   except ProcessLookupError:pass
  worker_log.close()
 with admin.connect() as db:
  searches=[dict(r) for r in db.execute(text("SELECT provider_id,domain,source_tier,media_type,disposition,result_sha256,observed_at FROM epidemic_discovery.source_discovery_search_results WHERE discovery_run_id=:run ORDER BY query_id,rank"),{"run":run_id}).mappings()]
  fetches=[dict(r) for r in db.execute(text("SELECT status,http_status,content_type,byte_size,domain,elapsed_ms,artifact_sha256,failure_code FROM epidemic_discovery.candidate_fetches WHERE discovery_run_id=:run ORDER BY attempted_at,id"),{"run":run_id}).mappings()]
  events=[dict(r) for r in db.execute(text("SELECT event_code,details,created_at FROM epidemic_discovery.discovery_run_events WHERE discovery_run_id=:run ORDER BY created_at,id"),{"run":run_id}).mappings()]
  candidates=[dict(r) for r in db.execute(text("""SELECT c.id candidate_id,cv.id candidate_version_id,rc.id review_case_id,rc.state review_state,rc.state_version,ar.sha256 artifact_sha256,ar.storage_key,sp.schema_sha256,mp.mapping_sha256,cv.terms_sha256 license_evidence_sha256,cv.license_status,ar.byte_size,sp.row_count,sp.column_count FROM epidemic_discovery.source_candidates c JOIN epidemic_discovery.source_candidate_versions cv ON cv.candidate_id=c.id JOIN epidemic_discovery.source_review_cases rc ON rc.candidate_version_id=cv.id JOIN epidemic_discovery.quarantine_artifacts ar ON ar.candidate_version_id=cv.id JOIN epidemic_discovery.source_schema_profiles sp ON sp.candidate_version_id=cv.id JOIN epidemic_discovery.source_mapping_proposals mp ON mp.candidate_version_id=cv.id AND mp.revision=rc.current_mapping_revision WHERE c.discovery_run_id=:run OR EXISTS(SELECT 1 FROM epidemic_discovery.discovery_run_candidate_links l WHERE l.discovery_run_id=:run AND l.candidate_version_id=cv.id) ORDER BY c.created_at"""),{"run":run_id}).mappings()]
 artifact_replay=False
 chosen_run=run_id
 if not candidates:
  artifact_replay=True
  with admin.connect() as db:
   card=dict(db.execute(text("""SELECT c.id candidate_id,cv.id candidate_version_id,rc.id review_case_id,rc.state review_state,rc.state_version,ar.sha256 artifact_sha256,ar.storage_key,sp.schema_sha256,mp.mapping_sha256,cv.terms_sha256 license_evidence_sha256,cv.license_status,ar.byte_size,sp.row_count,sp.column_count,COALESCE((SELECT l.discovery_run_id FROM epidemic_discovery.discovery_run_candidate_links l WHERE l.candidate_version_id=cv.id ORDER BY l.linked_at DESC,l.id DESC LIMIT 1),c.discovery_run_id) source_run_id FROM epidemic_discovery.source_candidates c JOIN epidemic_discovery.source_candidate_versions cv ON cv.candidate_id=c.id JOIN epidemic_discovery.source_review_cases rc ON rc.candidate_version_id=cv.id JOIN epidemic_discovery.quarantine_artifacts ar ON ar.candidate_version_id=cv.id JOIN epidemic_discovery.source_schema_profiles sp ON sp.candidate_version_id=cv.id JOIN epidemic_discovery.source_mapping_proposals mp ON mp.candidate_version_id=cv.id AND mp.revision=rc.current_mapping_revision WHERE c.publisher ILIKE '%Brazil%' OR c.publisher ILIKE '%DATASUS%' ORDER BY cv.created_at DESC LIMIT 1""")).mappings().one())
   candidates=[card];chosen_run=card.pop("source_run_id")
 card=candidates[0]
 artifact_path=(project/"storage/p1_9_demo/quarantine/sha256"/str(card["storage_key"])[:2]/str(card["storage_key"])).resolve()
 if not artifact_path.is_file() or hashlib.sha256(artifact_path.read_bytes()).hexdigest()!=str(card["artifact_sha256"]):raise RuntimeError("P1_10R_ARTIFACT_REPLAY_HASH_FAILED")
 binding={k:str(card[k]) for k in ("candidate_id","candidate_version_id","review_case_id","artifact_sha256","schema_sha256","mapping_sha256","license_evidence_sha256")};binding["discovery_run_id"]=str(chosen_run)
 mapping={"metric_code":"notification_record_count","display_name_zh":"登革热通知记录数","unit":"notifications","data_nature":"derived_aggregation","training_eligible":False,"national_aggregate":False,"source_date_semantics":"notification_date","geography_semantics":"notification_location","quality_status":"requires_review"}
 confirmations={k:True for k in ("not_trusted","license_pending","sample_partial","not_confirmed_cases","not_national","excluded_from_automation","whitelist_only","no_cross_source_sum")}
 try:
  with admin.begin() as db:
   decision=db.execute(text("SELECT epidemic_preview.submit_preview_decision(CAST(:case AS uuid),:reviewer,:reason,CAST(:idem AS uuid),:version,:confirmation,CAST(:confirmations AS jsonb),CAST(:binding AS jsonb),CAST(:binding_sha AS char(64)),CAST(:mapping AS jsonb),CAST(:mapping_sha AS char(64)),:environment,:test_only)"),{"case":card["review_case_id"],"reviewer":"p1-10r-isolated-e2e","reason":"隔离测试：只发布候选 Preview，不进入正式数据、训练、预测或每日更新。","idem":str(uuid.uuid4()),"version":card["state_version"],"confirmation":"发布候选预览","confirmations":canonical_json(confirmations),"binding":canonical_json(binding),"binding_sha":hashlib.sha256(canonical_json(binding).encode()).hexdigest(),"mapping":canonical_json(mapping),"mapping_sha":card["mapping_sha256"],"environment":"p1_10_test","test_only":True}).scalar_one()
   job=db.execute(text("SELECT epidemic_preview.queue_preview_publication(CAST(:decision AS uuid),CAST(:idem AS uuid))"),{"decision":decision,"idem":str(uuid.uuid4())}).scalar_one()
 except Exception as exc:
  original=getattr(exc,"orig",None);diag=getattr(original,"diag",None)
  primary=str(getattr(diag,"message_primary","") or type(exc).__name__)
  raise RuntimeError("P1_10R_DECISION_FAILED sqlstate="+str(getattr(original,"sqlstate","") or "")+" message="+primary) from None
 preview=sa.create_engine(preview_url,hide_parameters=True,pool_pre_ping=True)
 os.environ["APP_ENV"]="p1_10_test";os.environ["DISCOVERY_QUARANTINE_ROOT"]=str(project/"storage/p1_9_demo/quarantine/sha256")
 gate=PreviewGate.verify(preview);queued=time.monotonic()
 if not process_one(preview,gate,"p1-10r-isolated-e2e-worker"):raise RuntimeError("P1_10R_PREVIEW_NOT_CLAIMED")
 preview_ms=int((time.monotonic()-queued)*1000)
 with admin.connect() as db:
  job_row=dict(db.execute(text("SELECT id,state,attempt_count,failure_code,snapshot_id,publication_id FROM epidemic_preview.preview_publication_jobs WHERE id=:id"),{"id":job}).mappings().one())
  publication=dict(db.execute(text("SELECT id,publication_job_id,snapshot_id,observation_count,total_value,date_min,date_max,state_count,municipality_count,snapshot_sha256,publication_sha256,production_usable,training_eligible,national_aggregate,coverage_status,data_tier FROM epidemic_preview.active_preview_publications_v1 WHERE publication_job_id=:job"),{"job":job}).mappings().one())
  observations=int(db.execute(text("SELECT count(*) FROM epidemic_preview.preview_observations WHERE publication_job_id=:job"),{"job":job}).scalar_one())
  quality=dict(db.execute(text("SELECT status,pii_status,coverage_status,observation_count,total_value,assessment_sha256 FROM epidemic_preview.preview_quality_assessments WHERE publication_job_id=:job"),{"job":job}).mappings().one())
  lineage=dict(db.execute(text("SELECT artifact_sha256,schema_sha256,source_mapping_sha256,license_evidence_sha256,binding_sha256,contract_sha256,snapshot_sha256,assessment_sha256 FROM epidemic_preview.preview_lineage_v1 WHERE publication_job_id=:job"),{"job":job}).mappings().one())
  trusted_approval={"approvals":int(db.execute(text("SELECT count(*) FROM epidemic_discovery.source_review_decisions WHERE decision='APPROVED'")).scalar_one()),"promotions":int(db.execute(text("SELECT count(*) FROM epidemic_discovery.source_promotion_runs")).scalar_one()),"trusted_publications":int(db.execute(text("SELECT count(*) FROM epidemic_discovery.p1_9_publications")).scalar_one())}
 after=trusted(admin)
 if before!=after:raise RuntimeError("P1_10R_ISOLATED_TRUSTED_DRIFT")
 if job_row["state"]!="PREVIEW_PUBLISHED" or observations<1 or publication["production_usable"] or publication["training_eligible"]:raise RuntimeError("P1_10R_PREVIEW_E2E_FAILED")
 auth_file=project/"storage/p1_9_demo/secrets/login_hash.json"
 if not auth_file.is_file():raise RuntimeError("P1_10R_DEMO_AUTH_MISSING")
 service_env=os.environ.copy();service_env.update({"PYTHONPATH":str(stage/"src"),"APP_ENV":"p1_10_test","EPIDEMIC_INTERFACE_VERSION":"p1-10r3-v1","EPIDEMIC_DEPLOYMENT_COMMIT":os.environ["EXPECTED_HEAD"]})
 service_env.pop("http_proxy",None);service_env.pop("https_proxy",None);service_env.pop("HTTP_PROXY",None);service_env.pop("HTTPS_PROXY",None);service_env.pop("all_proxy",None);service_env.pop("ALL_PROXY",None)
 service_pids={}
 for label,port,module,url,auth_name in (("review",18031,"epidemic_core.discovery.review_app:create_app",review_url,"REVIEW"),("dashboard",18032,"epidemic_core.dashboard.app:create_app",dashboard_url,"DASHBOARD")):
  current=service_env.copy();current[auth_name+"_DATABASE_URL"]=url;current[auth_name+"_AUTH_FILE"]=str(auth_file)
  handle=open(logs/(label+".log"),"ab",buffering=0)
  proc=subprocess.Popen([python,"-m","uvicorn",module,"--factory","--host","127.0.0.1","--port",str(port)],cwd=stage,env=current,stdout=handle,stderr=subprocess.STDOUT,start_new_session=True)
  service_pids[label]=proc.pid;handle.close()
 for port in (18031,18032):
  deadline=time.monotonic()+45
  while True:
   try:
    with urllib.request.urlopen("http://127.0.0.1:"+str(port)+"/readyz",timeout=3) as response:
     payload=json.loads(response.read());status=response.status
    if status==200 and payload.get("ready") is True:break
   except Exception:pass
   if time.monotonic()>deadline:raise RuntimeError("P1_10R_ISOLATED_SERVICE_NOT_READY")
   time.sleep(.5)
 safe_events=[]
 for event in events:
  details=event.get("details") or {};safe_events.append({"event_code":event["event_code"],"provider_id":details.get("provider_id"),"domain":details.get("domain"),"network_request":details.get("network_request"),"http_status":details.get("http_status"),"elapsed_ms":details.get("elapsed_ms"),"result_count":details.get("result_count"),"reason_code":details.get("reason_code")})
 selected_domains=sorted({str(r.get("domain")) for r in searches+fetches if r.get("domain")})
 result={"pass":True,"database_name":name,"revision":revision,"clone_dump_sha256":dump_sha,"clone_dump_bytes":dump.stat().st_size,"discovery":{"run_id":str(run_id),"claim_delay_ms":claim_delay_ms,"state":run_row["state"],"terminal_reason_code":run_row["terminal_reason_code"],"progress":run_row["progress_json"],"events":safe_events,"searches":searches,"fetches":fetches,"candidate_count":len(candidates),"artifact_replay":artifact_replay,"selected_domains":selected_domains},"preview":{"decision_id":str(decision),"test_only":True,"job":job_row,"process_ms":preview_ms,"publication":publication,"observation_count":observations,"quality":quality,"lineage":lineage},"boundaries":{"trusted_clone_unchanged":True,"trusted_review_approval":trusted_approval,"production_usable":False,"training_eligible":False,"formal_database_modified":False},"services":{"review":{"port":18031,"ready":True,"pid":service_pids["review"]},"dashboard":{"port":18032,"ready":True,"pid":service_pids["dashboard"]}},"checks":checks,"credential_values_exposed":False}
 print(json.dumps(result,default=str))
finally:
 # Database and two loopback services intentionally remain until browser verification.
 pass
'''
        setup = (
            f"set -a; source {q(project + '/.secrets/p1_9_demo.env')}; set +a; "
            f"export PROJECT={q(project)} STAGE={q(stage)} PYTHON_BIN={q(python)} "
            f"POSTGRES_BIN={q(root + '/conda/envs/postgresql18/bin')} EXPECTED_HEAD={q(EXPECTED_HEAD)} "
            f"PYTHONPATH={q(stage + '/src')}"
        )
        result = _remote_json(client, python, setup, code, timeout=1800)
        runtime = {
            "database_name": result.pop("database_name"),
            "services": result["services"],
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "stage": stage,
        }
        ISOLATED_RUNTIME_STATE.write_text(json.dumps(runtime, indent=2), encoding="utf-8")
        (GATE_ROOT / "isolated_e2e_results.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    finally:
        client.close()


def live_discovery_action() -> None:
    state = _load_stage()
    stage = str(state["stage"])
    client, _gate = p19.a100.connect()
    try:
        root, project = p19.a100.paths(client)
        if not stage.startswith(project + "/storage/p1_10r/stage_"):
            raise SystemExit("P1_10R_UNSAFE_STAGE")
        python = f"{root}/conda/envs/epidemic-core/bin/python"
        code = r'''import datetime as dt,json,os,pathlib,re,signal,subprocess,time,uuid
import sqlalchemy as sa
from sqlalchemy import text

stage=pathlib.Path(os.environ["STAGE"]);project=pathlib.Path(os.environ["PROJECT"]);python=os.environ["PYTHON_BIN"]
admin=sa.create_engine(os.environ["DEMO_ADMIN_DATABASE_URL"],hide_parameters=True,pool_pre_ping=True)
dependencies={"extractor":(project.parent/"epidemic-extractor-runtime/extractor.py").is_file(),"extractor_python":pathlib.Path("/usr/bin/python3").is_file(),"quarantine":(project/"storage/p1_9_demo/quarantine/sha256").is_dir(),"account":bool(os.environ.get("DISCOVERY_EXTRACTOR_ACCOUNT"))}
with admin.begin() as db:
 disease=db.execute(text("SELECT id FROM epidemic.diseases WHERE canonical_name ILIKE '%Dengue%' ORDER BY id LIMIT 1")).scalar_one()
 request={"country_iso3":"BRA","disease_id":str(disease),"desired_metrics":["cases_new"],"time_window_kind":"latest_rolling_year","preferred_source_tiers":["NATIONAL_HEALTH","GOVERNMENT_OPEN_DATA","REGIONAL_HEALTH","INTERNATIONAL_PUBLIC_HEALTH","PUBLIC_HEALTH_RESEARCH"],"language_preferences":["pt","en"],"max_candidates":5,"retry_nonce":str(uuid.uuid4())}
 receipt=dict(db.execute(text("SELECT * FROM epidemic_discovery.submit_keyfree_discovery_v2(CAST(:request AS jsonb))"),{"request":json.dumps(request,separators=(",",":"))}).mappings().one())
run_id=receipt["run_id"];submitted=time.monotonic()
worker_env=os.environ.copy();worker_env.update({"APP_DATABASE_URL":os.environ["DEMO_APP_DATABASE_URL"],"PYTHONPATH":str(stage/"src"),"DISCOVERY_QUARANTINE_ROOT":str(project/"storage/p1_9_demo/quarantine/sha256"),"DISCOVERY_DIRECTORY_REGISTRY":str(stage/"config/source_discovery/directories.v1.json"),"DISCOVERY_EXTRACTOR_PATH":str(project.parent/"epidemic-extractor-runtime/extractor.py"),"DISCOVERY_EXTRACTOR_PYTHON":"/usr/bin/python3","DISCOVERY_DUCKDUCKGO_ENABLED":"0","DISCOVERY_PARALLEL_FREE_ENABLED":"0","DISCOVERY_BRAVE_ENABLED":"0"})
for key in ("BRAVE_SEARCH_API_KEY","BRAVE_API_KEY","http_proxy","https_proxy","HTTP_PROXY","HTTPS_PROXY","all_proxy","ALL_PROXY"):worker_env.pop(key,None)
log=stage/"storage/isolated-e2e/logs/live-discovery.log";log.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
handle=open(log,"ab",buffering=0);worker=subprocess.Popen([python,"-m","epidemic_core.discovery.worker"],cwd=stage,env=worker_env,stdout=handle,stderr=subprocess.STDOUT,start_new_session=True)
claim_ms=None
try:
 deadline=submitted+10
 while time.monotonic()<deadline:
  with admin.connect() as db:
   delta=db.execute(text("SELECT e.created_at-r.created_at FROM epidemic_discovery.discovery_run_events e JOIN epidemic_discovery.discovery_runs r ON r.id=e.discovery_run_id WHERE e.discovery_run_id=:run AND e.event_code='WORKER_CLAIMED' ORDER BY e.created_at,e.id LIMIT 1"),{"run":run_id}).scalar_one_or_none()
  if delta is not None:claim_ms=max(0,int(delta.total_seconds()*1000));break
  time.sleep(.2)
 if claim_ms is None:raise RuntimeError("P1_10R_LIVE_DISCOVERY_CLAIM_TIMEOUT")
 terminal={"REVIEW_READY","DISCOVERY_COMPLETE_NO_CANDIDATE","DISCOVERY_PARTIAL_FREE_PROVIDER_UNAVAILABLE","PROVIDER_DEGRADED","FAILED_TERMINAL","INVALID_REQUEST"};deadline=time.monotonic()+840
 while True:
  with admin.connect() as db:run=dict(db.execute(text("SELECT state,terminal_reason_code,progress_json,created_at,completed_at FROM epidemic_discovery.discovery_runs WHERE id=:run"),{"run":run_id}).mappings().one())
  if run["state"] in terminal:break
  if time.monotonic()>deadline:raise RuntimeError("P1_10R_LIVE_DISCOVERY_TERMINAL_TIMEOUT")
  time.sleep(1)
finally:
 try:os.killpg(worker.pid,signal.SIGTERM)
 except ProcessLookupError:pass
 try:worker.wait(timeout=15)
 except subprocess.TimeoutExpired:
  try:os.killpg(worker.pid,signal.SIGKILL)
  except ProcessLookupError:pass
 handle.close()
with admin.connect() as db:
 events=[dict(r) for r in db.execute(text("SELECT event_code,details,created_at FROM epidemic_discovery.discovery_run_events WHERE discovery_run_id=:run ORDER BY created_at,id"),{"run":run_id}).mappings()]
 searches=[dict(r) for r in db.execute(text("SELECT provider_id,domain,source_tier,media_type,disposition,result_sha256,observed_at FROM epidemic_discovery.source_discovery_search_results WHERE discovery_run_id=:run ORDER BY query_id,rank"),{"run":run_id}).mappings()]
 fetches=[dict(r) for r in db.execute(text("SELECT status,http_status,content_type,byte_size,domain,elapsed_ms,artifact_sha256,failure_code FROM epidemic_discovery.candidate_fetches WHERE discovery_run_id=:run ORDER BY attempted_at,id"),{"run":run_id}).mappings()]
 candidates=[dict(r) for r in db.execute(text("SELECT c.id candidate_id,cv.id candidate_version_id,ar.sha256 artifact_sha256,rc.state review_state FROM epidemic_discovery.source_candidates c JOIN epidemic_discovery.source_candidate_versions cv ON cv.candidate_id=c.id JOIN epidemic_discovery.source_review_cases rc ON rc.candidate_version_id=cv.id JOIN epidemic_discovery.quarantine_artifacts ar ON ar.candidate_version_id=cv.id WHERE c.discovery_run_id=:run OR EXISTS(SELECT 1 FROM epidemic_discovery.discovery_run_candidate_links l WHERE l.discovery_run_id=:run AND l.candidate_version_id=cv.id) ORDER BY c.created_at"),{"run":run_id}).mappings()]
safe_events=[]
for event in events:
 details=event.get("details") or {};safe_events.append({"event_code":event["event_code"],"provider_id":details.get("provider_id"),"domain":details.get("domain"),"network_request":details.get("network_request"),"http_status":details.get("http_status"),"elapsed_ms":details.get("elapsed_ms"),"result_count":details.get("result_count"),"reason_code":details.get("reason_code"),"error_code":details.get("error_code"),"exception_type":details.get("exception_type"),"message":details.get("message"),"error_sha256":details.get("error_sha256")})
domains=sorted({str(x.get("domain")) for x in searches+fetches+safe_events if x.get("domain")})
passed=claim_ms<10000 and run["state"]=="REVIEW_READY" and len(candidates)>=1 and any(e.get("network_request") is True and e.get("http_status")==200 for e in safe_events)
tail=log.read_text(encoding="utf-8",errors="replace").splitlines()[-60:]
tail=[re.sub(r"postgresql(?:\+\w+)?://[^\s'\"]+","[DATABASE_URL_REDACTED]",line) for line in tail]
print(json.dumps({"pass":passed,"run_id":str(run_id),"claim_delay_ms":claim_ms,"state":run["state"],"terminal_reason_code":run["terminal_reason_code"],"progress":run["progress_json"],"events":safe_events,"searches":searches,"fetches":fetches,"candidate_count":len(candidates),"candidate_artifact_sha256":str(candidates[0]["artifact_sha256"]) if candidates else None,"domains":domains,"dependencies":dependencies,"worker_log_tail":tail,"formal_database_modified":False},default=str))
'''
        setup = (
            f"set -a; source {q(project + '/.secrets/p1_9_demo.env')}; set +a; "
            f"export PROJECT={q(project)} STAGE={q(stage)} PYTHON_BIN={q(python)} PYTHONPATH={q(stage + '/src')}; "
            "if id -u epidemicextract >/dev/null 2>&1; then export DISCOVERY_EXTRACTOR_ACCOUNT=epidemicextract; fi"
        )
        result = _remote_json(client, python, setup, code, timeout=1200)
        (GATE_ROOT / "live_discovery_results.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        if not result.get("pass"):
            raise SystemExit("P1_10R_LIVE_DISCOVERY_GATE_FAILED")
    finally:
        client.close()


def cleanup_isolated_action() -> None:
    state = _load_stage()
    stage = str(state["stage"])
    runtime = json.loads(ISOLATED_RUNTIME_STATE.read_text(encoding="utf-8")) if ISOLATED_RUNTIME_STATE.is_file() else {}
    if not runtime and (GATE_ROOT / "isolated_e2e_results.json").is_file():
        runtime = json.loads((GATE_ROOT / "isolated_e2e_results.json").read_text(encoding="utf-8"))
    pids = [str(item.get("pid")) for item in runtime.get("services", {}).values() if item.get("pid")]
    client, _gate = p19.a100.connect()
    try:
        root, project = p19.a100.paths(client)
        if not stage.startswith(project + "/storage/p1_10r/stage_"):
            raise SystemExit("P1_10R_UNSAFE_STAGE")
        python = f"{root}/conda/envs/epidemic-core/bin/python"
        code = r'''import json,os,pathlib,re,signal,time
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import make_url

stage=pathlib.Path(os.environ["STAGE"]);stopped=0
for raw in filter(None,os.environ.get("SERVICE_PIDS","").split(",")):
 try:
  pid=int(raw);proc=pathlib.Path("/proc")/raw;cmd=(proc/"cmdline").read_bytes().replace(b"\0",b" ").decode("utf-8","replace");cwd=(proc/"cwd").resolve()
 except (ValueError,FileNotFoundError,ProcessLookupError):continue
 if cwd==stage and "uvicorn" in cmd and ("18031" in cmd or "18032" in cmd):
  try:os.killpg(pid,signal.SIGTERM);stopped+=1
  except ProcessLookupError:pass
time.sleep(1)
source=make_url(os.environ["DEMO_ADMIN_DATABASE_URL"]);cluster=sa.create_engine(source.set(database="postgres"),isolation_level="AUTOCOMMIT",hide_parameters=True)
with cluster.connect() as db:
 names=[str(x) for x in db.execute(text("SELECT datname FROM pg_database WHERE datname LIKE 'epidemic_p1_10r_e2e_%'")).scalars()]
 for name in names:
  if not re.fullmatch(r"epidemic_p1_10r_e2e_[0-9a-f]{10}",name):raise RuntimeError("P1_10R_UNSAFE_DATABASE_NAME")
  db.execute(text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=:name"),{"name":name})
  db.exec_driver_sql('DROP DATABASE "'+name+'"')
cluster.dispose();print(json.dumps({"stopped_services":stopped,"dropped_isolated_databases":len(names),"formal_database_modified":False}))
'''
        setup = (
            f"set -a; source {q(project + '/.secrets/p1_9_demo.env')}; set +a; "
            f"export STAGE={q(stage)} SERVICE_PIDS={q(','.join(pids))}"
        )
        result = _remote_json(client, python, setup, code, timeout=300)
        ISOLATED_RUNTIME_STATE.unlink(missing_ok=True)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    finally:
        client.close()


def deploy_action() -> None:
    capture = json.loads((GATE_ROOT / "capture.json").read_text(encoding="utf-8"))
    client, _gate = p19.a100.connect()
    try:
        root, project = p19.a100.paths(client)
        head = p19.a100.checked(client, f"git -C {q(project)} rev-parse HEAD")
        if head != EXPECTED_HEAD:
            raise SystemExit("P1_10R_DEPLOY_HEAD_CHANGED")
        if p19.a100.checked(client, f"git -C {q(project)} diff --cached --name-only"):
            raise SystemExit("P1_10R_DEPLOY_INDEX_NOT_EMPTY")
        sftp = client.open_sftp()
        temporary: list[tuple[str, str, int]] = []
        try:
            for relative in CHANGE_FILES:
                source = WORK_ROOT / relative
                if not source.is_file():
                    raise SystemExit(f"P1_10R_DEPLOY_SOURCE_MISSING:{relative}")
                remote = f"{project}/{relative}"
                expected = capture["files"].get(relative)
                try:
                    with sftp.file(remote, "rb") as handle:
                        current = handle.read()
                    exists = True
                except OSError:
                    current = b""
                    exists = False
                desired_sha = hashlib.sha256(source.read_bytes()).hexdigest()
                if expected and expected.get("exists"):
                    if not exists or hashlib.sha256(current).hexdigest() not in {expected["sha256"], desired_sha}:
                        raise SystemExit(f"P1_10R_DEPLOY_CONCURRENT_CHANGE:{relative}")
                    mode = int(expected.get("mode", 0o644))
                else:
                    if exists and hashlib.sha256(current).hexdigest() != desired_sha:
                        raise SystemExit(f"P1_10R_DEPLOY_UNEXPECTED_EXISTING:{relative}")
                    mode = 0o755 if relative.endswith(".sh") else 0o644
                if exists and hashlib.sha256(current).hexdigest() == desired_sha:
                    continue
                token = secrets.token_hex(4)
                tmp = f"{remote}.p1_10r_{token}"
                sftp.put(str(source), tmp)
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
        target_args = " ".join(q(item) for item in CHANGE_FILES)
        p19.a100.checked(client, f"git -C {q(project)} diff --check -- {target_args}", timeout=300)
        python = f"{root}/conda/envs/epidemic-core/bin/python"
        migration_code = r'''import hashlib,json,os,subprocess
from sqlalchemy.engine import make_url
url=make_url(os.environ["MIGRATION_DATABASE_URL"]).set(username="epidemicdb",password=os.environ["POSTGRES_ADMIN_PASSWORD"])
env=os.environ.copy();env["MIGRATION_DATABASE_URL"]=url.render_as_string(hide_password=False)
result=subprocess.run([os.environ["PYTHON_BIN"],"-m","alembic","upgrade","head"],cwd=os.environ["PROJECT"],env=env,text=True,capture_output=True,timeout=900)
if result.returncode:raise RuntimeError("P1_10R_OWNER_MIGRATION_FAILED:"+hashlib.sha256((result.stdout+result.stderr).encode()).hexdigest())
print(json.dumps({"upgraded":True,"revision":"0019_p1_10r_formal_preview_http500"}))
'''
        migration_setup = (
            f"source {q(project + '/scripts/database/_env.sh')}; set -a; "
            f"source {q(project + '/.secrets/postgres.env')}; set +a; "
            f"export PROJECT={q(project)} PYTHON_BIN={q(python)}"
        )
        _remote_json(client, python, migration_setup, migration_code, timeout=1200)
        service_commands = (
            "scripts/source_review/stop_source_review.sh",
            "scripts/dashboard/stop_dashboard.sh",
            "scripts/source_review/install_runtime.sh",
            "scripts/dashboard/install_runtime.sh",
            "scripts/source_review/start_source_review.sh",
            "scripts/dashboard/start_dashboard.sh",
        )
        service_results: dict[str, bool] = {}
        for relative in service_commands:
            rc, _out, _err = p19.a100.run(client, q(f"{project}/{relative}"), timeout=300)
            service_results[relative] = rc == 0
            if rc != 0:
                raise SystemExit(f"P1_10R_DEPLOY_SERVICE_FAILED:{relative}")
        result = {
            "uploaded_paths": list(CHANGE_FILES),
            "migration": "0019_p1_10r_formal_preview_http500",
            "service_results": service_results,
            "formal_preview_authorization_created": False,
            "trusted_write_performed": False,
            "preexisting_dirty_preserved": True,
        }
        (GATE_ROOT / "formal_deploy_results.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
        print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
    finally:
        client.close()


def migration_diagnose_action() -> None:
    client, _gate = p19.a100.connect()
    try:
        root, project = p19.a100.paths(client)
        command = (
            f"cd {q(project)}; source {q(project + '/scripts/database/_env.sh')}; set -a; "
            f"source {q(project + '/.secrets/postgres.env')}; set +a; "
            f"{q(root + '/conda/envs/epidemic-core/bin/python')} -m alembic upgrade head"
        )
        rc, out, err = p19.a100.run(client, f"bash -lc {q(command)}", timeout=900)
        safe = _safe_log_text(out + "\n" + err).replace(project, "[project]").replace(root, "[data-root]")
        code = r'''import json,os
import sqlalchemy as sa
from sqlalchemy import text
engine=sa.create_engine(os.environ["MIGRATION_DATABASE_URL"],hide_parameters=True)
with engine.connect() as db:
 result={"current_user":db.execute(text("SELECT current_user")).scalar_one(),"schemas":[dict(x) for x in db.execute(text("SELECT n.nspname schema_name,r.rolname owner,has_schema_privilege(current_user,n.oid,'USAGE') can_use FROM pg_namespace n JOIN pg_roles r ON r.oid=n.nspowner WHERE n.nspname IN ('epidemic','epidemic_discovery','epidemic_preview') ORDER BY n.nspname")).mappings()],"tables":[dict(x) for x in db.execute(text("SELECT n.nspname schema_name,c.relname,r.rolname owner,has_table_privilege(current_user,c.oid,'SELECT') can_select FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_roles r ON r.oid=c.relowner WHERE n.nspname='epidemic_discovery' AND c.relname IN ('source_review_cases','source_schema_profiles','source_schema_columns','source_candidates','discovery_run_candidate_links') ORDER BY c.relname")).mappings()],"epidemicdb_version_privileges":db.execute(text("SELECT has_table_privilege('epidemicdb','epidemic.alembic_version','SELECT,UPDATE')")).scalar_one(),"epidemicdb_can_set_migrator":db.execute(text("SELECT pg_has_role('epidemicdb','epidemic_migrator','MEMBER')")).scalar_one()}
print(json.dumps(result))
'''
        setup = (
            f"source {q(project + '/scripts/database/_env.sh')}; set -a; "
            f"source {q(project + '/.secrets/postgres.env')}; set +a"
        )
        ownership = _remote_json(client, f"{root}/conda/envs/epidemic-core/bin/python", setup, code)
        print(json.dumps({"returncode": rc, "tail": safe.splitlines()[-80:], "ownership": ownership}, ensure_ascii=True, indent=2))
    finally:
        client.close()


def formal_verify_action() -> None:
    before = json.loads((GATE_ROOT / "production_scene.json").read_text(encoding="utf-8"))["database"]
    client, _gate = p19.a100.connect()
    try:
        root, project = p19.a100.paths(client)
        python = f"{root}/conda/envs/epidemic-core/bin/python"
        code = r'''import json,os,pathlib,urllib.request
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import make_url
from epidemic_core.dashboard.app import _json_response,_query
from epidemic_core.p1_10.dashboard_app import install_routes

before=json.loads(os.environ["BEFORE"]);url=make_url(os.environ["MIGRATION_DATABASE_URL"]).set(username="epidemicdb",password=os.environ["POSTGRES_ADMIN_PASSWORD"])
owner=sa.create_engine(url,hide_parameters=True);dashboard=sa.create_engine(os.environ["DASHBOARD_DATABASE_URL"],hide_parameters=True)
trusted_tables=("data_sources","query_requests","retrieval_runs","source_fetches","raw_artifacts","normalized_observations","quality_assessments","dataset_snapshots","dataset_snapshot_observations")
preview_tables=("preview_review_decisions","preview_publication_jobs","preview_mapping_contracts","preview_staging_observations","preview_observations","preview_quality_assessments","preview_snapshots","preview_snapshot_observations","preview_dashboard_publications","preview_publication_events")
def denied(role,statement):
 try:
  with owner.connect() as db:
   tx=db.begin();db.execute(text('SET LOCAL ROLE "'+role+'"'));db.execute(text(statement));tx.rollback()
  return False
 except Exception:return True
with owner.connect() as db:
 migration=db.execute(text("SELECT version_num FROM epidemic.alembic_version")).scalar_one()
 trusted={t:db.execute(text('SELECT count(*) FROM epidemic."'+t+'"')).scalar_one() for t in trusted_tables}
 previews={t:db.execute(text('SELECT count(*) FROM epidemic_preview."'+t+'"')).scalar_one() for t in preview_tables}
 snapshots=json.loads(json.dumps([dict(x) for x in db.execute(text("SELECT id,row_count,content_sha256 FROM epidemic.dataset_snapshots ORDER BY created_at,id")).mappings()],default=str))
 schedules=json.loads(json.dumps([dict(x) for x in db.execute(text("SELECT schedule_id,schedule_name,enabled,last_success_at,next_due_at,state_version,consecutive_failures FROM epidemic.daily_update_schedules ORDER BY schedule_name")).mappings()],default=str))
 user_run=dict(db.execute(text("SELECT discovery_run_id,state,candidate_count,created_at,updated_at FROM epidemic_discovery.review_discovery_runs_v3 WHERE discovery_run_id='8e34416b-718a-4ffa-8070-e28e489c5c91'")).mappings().one())
 links=db.execute(text("SELECT count(*) FROM epidemic_discovery.discovery_run_candidate_links WHERE discovery_run_id=:id"),{"id":user_run["discovery_run_id"]}).scalar_one()
 marker=bool(db.execute(text("SELECT production AND NOT disposable AND database_name=current_database() FROM epidemic_preview.environment_markers WHERE environment='production'")).scalar_one())
 permissions={
  "review_safe_columns":db.execute(text("SELECT has_table_privilege('epidemicreview','epidemic_preview.preview_safe_source_columns_v1','SELECT')")).scalar_one(),
  "review_candidate_links":db.execute(text("SELECT has_table_privilege('epidemicreview','epidemic_preview.preview_discovery_candidate_links_v1','SELECT')")).scalar_one(),
  "review_readiness":db.execute(text("SELECT has_table_privilege('epidemicreview','epidemic_preview.runtime_readiness_v1','SELECT')")).scalar_one(),
  "dashboard_readiness":db.execute(text("SELECT has_table_privilege('epidemic_dashboard_ro','epidemic_preview.runtime_readiness_v1','SELECT')")).scalar_one(),
 }
app=FastAPI();install_routes(app,dashboard,lambda _request:True,lambda:JSONResponse({"detail":"AUTHENTICATION_REQUIRED"},401),_query,_json_response)
with TestClient(app) as test:
 response=test.get("/api/v1/preview/options");body=response.json()
empty_http={"status":response.status_code,"data_tier":body.get("data_tier"),"state":body.get("state"),"sources":body.get("sources"),"active_publication":body.get("active_publication")}
services={}
for name,port in (("dashboard",8020),("review_console",8021)):
 try:
  with urllib.request.urlopen(f"http://127.0.0.1:{port}/readyz",timeout=5) as response:services[name]={"status":response.status,"body":json.loads(response.read())}
 except Exception as exc:services[name]={"status":0,"error_type":type(exc).__name__}
pids={}
for name,path in (("dashboard",pathlib.Path(os.environ["ROOT"])/"epidemic-dashboard-runtime/run/dashboard.pid"),("review_console",pathlib.Path(os.environ["ROOT"])/"epidemic-source-review-runtime/run/review.pid")):
 pid=int(path.read_text().strip());env=(pathlib.Path("/proc")/str(pid)/"environ").read_bytes();names={x.split(b"=",1)[0] for x in env.split(b"\0") if b"=" in x};pids[name]={"pid_recorded":True,"proxy_variables_present":bool(names&{b"http_proxy",b"https_proxy",b"HTTP_PROXY",b"HTTPS_PROXY",b"all_proxy",b"ALL_PROXY"})}
before_snapshots=[{"id":x["id"],"row_count":x["row_count"],"content_sha256":x["content_sha256"]} for x in before["snapshots"]]
result={"migration":migration,"marker_production":marker,"trusted_counts":trusted,"trusted_counts_unchanged":trusted==before["trusted_counts"],"snapshots":snapshots,"snapshot_hashes_unchanged":snapshots==before_snapshots,"daily_schedules":schedules,"daily_schedules_unchanged":schedules==before["daily_schedules"],"preview_counts":previews,"formal_preview_all_zero":not any(previews.values()),"user_run":{"state":user_run["state"],"candidate_count":user_run["candidate_count"],"original_timestamps_preserved":str(user_run["created_at"])==before["recent_discovery_runs"][0]["created_at"] and str(user_run["updated_at"])==before["recent_discovery_runs"][0]["updated_at"],"candidate_links":links},"permissions":permissions,"negative_permissions":{"dashboard_raw_denied":denied("epidemic_dashboard_ro","SELECT count(*) FROM epidemic_discovery.quarantine_artifacts"),"review_raw_columns_denied":denied("epidemicreview","SELECT count(*) FROM epidemic_discovery.source_schema_columns"),"daily_preview_denied":denied("epidemic_app","SELECT count(*) FROM epidemic_preview.preview_observations")},"preview_empty_http":empty_http,"services":services,"processes":pids,"formal_preview_authorization_created":False,"trusted_write_performed":False}
result["pass"]=migration=="0019_p1_10r_formal_preview_http500" and marker and result["trusted_counts_unchanged"] and result["snapshot_hashes_unchanged"] and result["daily_schedules_unchanged"] and result["formal_preview_all_zero"] and user_run["state"]=="REVIEW_READY" and user_run["candidate_count"]==1 and links==1 and all(permissions.values()) and all(result["negative_permissions"].values()) and empty_http=={"status":200,"data_tier":"REVIEW_PREVIEW","state":"EMPTY","sources":[],"active_publication":None} and all(x.get("status")==200 and x.get("body",{}).get("ready") for x in services.values()) and not any(x["proxy_variables_present"] for x in pids.values())
print(json.dumps(result,default=str))
owner.dispose();dashboard.dispose()
'''
        setup = (
            f"source {q(project + '/scripts/database/_env.sh')}; set -a; "
            f"source {q(project + '/.secrets/postgres.env')}; "
            f"source {q(root + '/epidemic-dashboard-runtime/secrets/dashboard.env')}; set +a; "
            f"export ROOT={q(root)} BEFORE={q(json.dumps(before, default=str))} "
            f"PYTHONPATH={q(project + '/src')}"
        )
        result = _remote_json(client, python, setup, code, timeout=600)
        (GATE_ROOT / "formal_verification_results.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
        print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
        if not result.get("pass"):
            raise SystemExit("P1_10R_FORMAL_VERIFY_FAILED")
    finally:
        client.close()


def launcher_gate_action() -> None:
    client, gate = p19.a100.connect()
    try:
        root, project = p19.a100.paths(client)
        python = f"{root}/conda/envs/epidemic-core/bin/python"
        code = r'''import json,os,subprocess,urllib.request
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import make_url
project=os.environ["PROJECT"];url=make_url(os.environ["MIGRATION_DATABASE_URL"]).set(username="epidemicdb",password=os.environ["POSTGRES_ADMIN_PASSWORD"])
engine=sa.create_engine(url,hide_parameters=True)
with engine.connect() as db:
 migration=db.execute(text("SELECT version_num FROM epidemic.alembic_version")).scalar_one();marker=bool(db.execute(text("SELECT production AND NOT disposable AND database_name=current_database() FROM epidemic_preview.environment_markers WHERE environment='production'")).scalar_one())
subject=subprocess.run(["git","-C",project,"show","-s","--format=%s","HEAD"],text=True,capture_output=True,check=True).stdout.strip();ready={}
for name,port in (("dashboard",8020),("review",8021)):
 try:
  with urllib.request.urlopen(f"http://127.0.0.1:{port}/readyz",timeout=5) as response:ready[name]=response.status==200 and json.loads(response.read()).get("ready") is True
 except Exception:ready[name]=False
proxy=any(os.environ.get(name) for name in ("http_proxy","https_proxy","HTTP_PROXY","HTTPS_PROXY","all_proxy","ALL_PROXY"))
print(json.dumps({"migration":migration,"production_marker":marker,"commit_subject_match":subject=="fix(p1-10): repair formal discovery and preview dashboard","services_ready":ready,"proxy_variables_present":proxy}))
'''
        setup = (
            f"source {q(project + '/scripts/database/_env.sh')}; set -a; "
            f"source {q(project + '/.secrets/postgres.env')}; set +a; export PROJECT={q(project)}; "
            "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY"
        )
        result = _remote_json(client, python, setup, code, timeout=300)
        result["target_identity_verified"] = bool(gate.get("stable_second_connection"))
        passed = (
            result["migration"] == "0019_p1_10r_formal_preview_http500"
            and result["production_marker"]
            and result["commit_subject_match"]
            and all(result["services_ready"].values())
            and not result["proxy_variables_present"]
            and result["target_identity_verified"]
        )
        print(json.dumps({"pass": passed, "gate": result}, ensure_ascii=True, indent=2, sort_keys=True))
        if not passed:
            raise SystemExit("P1_10R_LAUNCHER_GATE_FAILED")
    finally:
        client.close()


def capture_index_files_action() -> None:
    client, _gate = p19.a100.connect()
    try:
        _root, project = p19.a100.paths(client)
        if p19.a100.checked(client, f"git -C {q(project)} rev-parse HEAD") != EXPECTED_HEAD:
            raise SystemExit("P1_10R_INDEX_HEAD_CHANGED")
        INDEX_ROOT.mkdir(parents=True, exist_ok=True)
        for relative in OVERLAP_FILES:
            encoded = p19.a100.checked(
                client,
                f"git -C {q(project)} show {q('HEAD:' + relative)} | base64 -w0",
                timeout=120,
            )
            payload = base64.b64decode(encoded)
            target = INDEX_ROOT / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
        common_path = INDEX_ROOT / "scripts/dashboard/_common.sh"
        common = common_path.read_text(encoding="utf-8")
        if "dashboard_ready()" not in common:
            common += (
                "\ndashboard_ready() {\n"
                "  curl --silent --show-error --fail --noproxy '*' --connect-timeout 2 --max-time 5 \\\n"
                '    "http://$HOST:$PORT/readyz" >/dev/null\n'
                "}\n"
            )
        common_path.write_text(common, encoding="utf-8", newline="\n")
        start_path = INDEX_ROOT / "scripts/dashboard/start_dashboard.sh"
        start = start_path.read_text(encoding="utf-8")
        start = start.replace("dashboard_alive && dashboard_health", "dashboard_alive && dashboard_ready")
        marker = "for key in http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY GIT_CONFIG; do unset \"$key\" || true; done\n"
        if "EPIDEMIC_INTERFACE_VERSION=p1-10r-v1" not in start:
            start = start.replace(
                marker,
                marker
                + 'DEPLOYMENT_COMMIT=$(git -C "$PROJECT_ROOT" rev-parse HEAD)\n'
                + "export DEPLOYMENT_COMMIT\n",
            )
            start = start.replace(
                "export DASHBOARD_AUTH_FILE='$AUTH_FILE' PYTHONPATH='$RUNTIME/src'; exec",
                "export DASHBOARD_AUTH_FILE='$AUTH_FILE' PYTHONPATH='$RUNTIME/src' APP_ENV=production "
                "EPIDEMIC_DEPLOYMENT_COMMIT='$DEPLOYMENT_COMMIT' EPIDEMIC_INTERFACE_VERSION=p1-10r-v1; exec",
            )
        start_path.write_text(start, encoding="utf-8", newline="\n")
        print(json.dumps({"prepared": list(OVERLAP_FILES), "head": EXPECTED_HEAD}, ensure_ascii=True, indent=2))
    finally:
        client.close()


def upload_reports_action() -> None:
    client, _gate = p19.a100.connect()
    try:
        _root, project = p19.a100.paths(client)
        remote_root = f"{project}/reports/p1_10r_formal_preview_http500"
        p19.a100.checked(client, f"mkdir -p {q(remote_root)} && chmod 750 {q(remote_root)}")
        sftp = client.open_sftp()
        try:
            for name in REPORT_FILES:
                source = REPORT_ROOT / name
                if not source.is_file():
                    raise SystemExit(f"P1_10R_REPORT_MISSING:{name}")
                remote = f"{remote_root}/{name}"
                try:
                    with sftp.file(remote, "rb") as handle:
                        current = handle.read()
                except OSError:
                    current = b""
                desired = source.read_bytes()
                if current == desired:
                    continue
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
    client, _gate = p19.a100.connect()
    try:
        _root, project = p19.a100.paths(client)
        if p19.a100.checked(client, f"git -C {q(project)} rev-parse HEAD") != EXPECTED_HEAD:
            raise SystemExit("P1_10R_COMMIT_HEAD_CHANGED")
        report_paths = [f"reports/p1_10r_formal_preview_http500/{name}" for name in REPORT_FILES]
        expected = sorted(CHANGE_FILES + tuple(report_paths))
        already_staged = p19.a100.checked(client, f"git -C {q(project)} diff --cached --name-only").splitlines()
        if not set(already_staged).issubset(expected):
            raise SystemExit("P1_10R_COMMIT_INDEX_HAS_UNRELATED_PATHS")
        ordinary = [item for item in CHANGE_FILES if item not in OVERLAP_FILES]
        p19.a100.checked(client, f"git -C {q(project)} add -- " + " ".join(q(x) for x in ordinary))
        p19.a100.checked(client, f"git -C {q(project)} add -f -- " + " ".join(q(x) for x in report_paths))
        blob_root = f"{project}/storage/p1_10r/index_{secrets.token_hex(5)}"
        p19.a100.checked(client, f"mkdir -p {q(blob_root)} && chmod 700 {q(blob_root)}")
        sftp = client.open_sftp()
        try:
            for relative in OVERLAP_FILES:
                source = INDEX_ROOT / relative
                if not source.is_file():
                    raise SystemExit(f"P1_10R_INDEX_FILE_MISSING:{relative}")
                remote = f"{blob_root}/{Path(relative).name}"
                sftp.put(str(source), remote)
                sftp.chmod(remote, 0o600)
                blob = p19.a100.checked(client, f"git -C {q(project)} hash-object -w -- {q(remote)}")
                mode_line = p19.a100.checked(client, f"git -C {q(project)} ls-files -s -- {q(relative)}")
                mode = mode_line.split()[0]
                p19.a100.checked(
                    client,
                    f"git -C {q(project)} update-index --add --cacheinfo {q(mode + ',' + blob + ',' + relative)}",
                )
                sftp.remove(remote)
            sftp.rmdir(blob_root)
        finally:
            sftp.close()
        staged = p19.a100.checked(client, f"git -C {q(project)} diff --cached --name-only").splitlines()
        if sorted(staged) != expected:
            raise SystemExit("P1_10R_COMMIT_STAGE_SCOPE_MISMATCH")
        p19.a100.checked(client, f"git -C {q(project)} diff --cached --check", timeout=300)
        python = p19.a100.paths(client)[0] + "/conda/envs/epidemic-core/bin/python"
        scan_code = r'''import hashlib,json,os,re,subprocess
project=os.environ["PROJECT"];paths=json.loads(os.environ["PATHS"])
rules={"private_key":r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----","cloud_token":r"(?:AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9_]{30,}|sk-[A-Za-z0-9]{24,})","credential_url":r"(?:postgresql|https?)://[^\s:/]+:[^\s/@]+@","tls_bypass":r"curl\s+-k(?:\s|$)|wget\s+--no-check-certificate|GIT_SSL_NO_VERIFY\s*=\s*(?:1|true)|pip\s+[^\n]*--trusted-host","proxy_literal":r"(?i)(?:http|https|all)_proxy\s*=\s*['\"]?(?:https?|socks)://"}
findings=[];large=[]
for path in paths:
 data=subprocess.run(["git","-C",project,"show",":"+path],capture_output=True,check=True).stdout
 if len(data)>2*1024*1024:large.append({"path":path,"bytes":len(data)})
 if b"\0" in data:continue
 text=data.decode("utf-8","replace")
 for name,pattern in rules.items():
  for match in re.finditer(pattern,text,re.I|re.M):findings.append({"path":path,"line":text.count("\n",0,match.start())+1,"rule":name,"match_sha256":hashlib.sha256(match.group().encode()).hexdigest()})
print(json.dumps({"findings":findings,"large_files":large}))
'''
        scan = _remote_json(
            client,
            python,
            f"export PROJECT={q(project)} PATHS={q(json.dumps(staged))}",
            scan_code,
            timeout=600,
        )
        if scan["findings"] or scan["large_files"]:
            raise SystemExit("P1_10R_COMMIT_SECURITY_SCAN_FAILED")
        p19.a100.checked(
            client,
            f"git -C {q(project)} commit -m {q('fix(p1-10): repair formal discovery and preview dashboard')}",
            timeout=600,
        )
        commit_hash = p19.a100.checked(client, f"git -C {q(project)} rev-parse HEAD")
        subject = p19.a100.checked(client, f"git -C {q(project)} show -s --format=%s HEAD")
        staged_after = p19.a100.checked(client, f"git -C {q(project)} diff --cached --name-only")
        status_after = p19.a100.checked(client, f"git -C {q(project)} status --porcelain=v2 -uall")
        dirty_after = {_status_path(line) for line in status_after.splitlines() if line}
        preserved = set(capture["dirty_paths"]).issubset(dirty_after)
        result = {
            "commit": commit_hash,
            "subject": subject,
            "path_count": len(staged),
            "staged_after": bool(staged_after),
            "preexisting_dirty_preserved": preserved,
            "secret_findings": 0,
            "large_files": 0,
            "push": False,
        }
        (GATE_ROOT / "git_commit_results.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
        print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
        if subject != "fix(p1-10): repair formal discovery and preview dashboard" or staged_after or not preserved:
            raise SystemExit("P1_10R_COMMIT_POSTCONDITION_FAILED")
    finally:
        client.close()


def commit_diagnose_action() -> None:
    client, _gate = p19.a100.connect()
    try:
        _root, project = p19.a100.paths(client)
        staged = p19.a100.checked(client, f"git -C {q(project)} diff --cached --name-only").splitlines()
        checks = {}
        for name, command in {
            "diff_check": f"git -C {q(project)} diff --cached --check",
            "diff_stat": f"git -C {q(project)} diff --cached --stat",
            "overlap_index": f"git -C {q(project)} ls-files -s -- " + " ".join(q(x) for x in OVERLAP_FILES),
        }.items():
            rc, out, err = p19.a100.run(client, command, timeout=300)
            checks[name] = {"returncode": rc, "tail": _safe_log_text(out + err).splitlines()[-30:]}
        print(json.dumps({"staged_count": len(staged), "checks": checks}, ensure_ascii=True, indent=2))
    finally:
        client.close()


def finalize_runtime_action() -> None:
    commit = json.loads((GATE_ROOT / "git_commit_results.json").read_text(encoding="utf-8"))["commit"]
    client, _gate = p19.a100.connect()
    try:
        _root, project = p19.a100.paths(client)
        if p19.a100.checked(client, f"git -C {q(project)} rev-parse HEAD") != commit:
            raise SystemExit("P1_10R_FINALIZE_COMMIT_MISMATCH")
        commands = (
            "scripts/source_review/stop_source_review.sh",
            "scripts/dashboard/stop_dashboard.sh",
            "scripts/source_review/install_runtime.sh",
            "scripts/dashboard/install_runtime.sh",
            "scripts/source_review/start_source_review.sh",
            "scripts/dashboard/start_dashboard.sh",
        )
        for relative in commands:
            rc, _out, _err = p19.a100.run(client, q(f"{project}/{relative}"), timeout=300)
            if rc != 0:
                raise SystemExit(f"P1_10R_FINALIZE_SERVICE_FAILED:{relative}")
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
            "preview_worker": "scripts/preview_publication_worker/status_preview_publication_worker.sh",
        }
        services = {
            name: p19.a100.run(client, q(f"{project}/{relative}"), timeout=180)[0] == 0
            for name, relative in service_paths.items()
        }
        python = p19.a100.paths(client)[0] + "/conda/envs/epidemic-core/bin/python"
        code = r'''import json,os,pathlib
commit=os.environ["COMMIT"];root=pathlib.Path(os.environ["ROOT"]);result={}
for name,path in (("dashboard",root/"epidemic-dashboard-runtime/run/dashboard.pid"),("review_console",root/"epidemic-source-review-runtime/run/review.pid")):
 pid=int(path.read_text().strip());env=(pathlib.Path("/proc")/str(pid)/"environ").read_bytes();values={x.split(b"=",1)[0]:x.split(b"=",1)[1] for x in env.split(b"\0") if b"=" in x};result[name]={"commit_match":values.get(b"EPIDEMIC_DEPLOYMENT_COMMIT",b"").decode()==commit,"interface_match":values.get(b"EPIDEMIC_INTERFACE_VERSION",b"").decode()=="p1-10r-v1","proxy_variables_present":bool(set(values)&{b"http_proxy",b"https_proxy",b"HTTP_PROXY",b"HTTPS_PROXY",b"all_proxy",b"ALL_PROXY"})}
print(json.dumps(result))
'''
        runtime = _remote_json(
            client,
            python,
            f"export ROOT={q(p19.a100.paths(client)[0])} COMMIT={q(commit)}",
            code,
        )
        result = {
            "commit": commit,
            "services": services,
            "runtime": runtime,
            "staged_empty": not bool(
                p19.a100.checked(client, f"git -C {q(project)} diff --cached --name-only")
            ),
        }
        result["pass"] = (
            all(services.values())
            and result["staged_empty"]
            and all(
                row["commit_match"] and row["interface_match"] and not row["proxy_variables_present"]
                for row in runtime.values()
            )
        )
        (GATE_ROOT / "final_runtime_results.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
        print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
        if not result["pass"]:
            raise SystemExit("P1_10R_FINAL_RUNTIME_FAILED")
    finally:
        client.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action",
        choices=(
            "capture",
            "scene",
            "stage",
            "stage-format",
            "stage-tests",
            "full-regression",
            "migration19-test",
            "isolated-e2e",
            "live-discovery",
            "cleanup-isolated",
            "deploy",
            "formal-verify",
            "launcher-gate",
            "migration-diagnose",
            "capture-index-files",
            "upload-reports",
            "commit",
            "commit-diagnose",
            "finalize-runtime",
        ),
    )
    args = parser.parse_args()
    if args.action == "capture":
        capture_action()
    elif args.action == "scene":
        scene_action()
    elif args.action == "stage":
        stage_action()
    elif args.action == "stage-tests":
        stage_tests_action()
    elif args.action == "full-regression":
        full_regression_action()
    elif args.action == "stage-format":
        stage_format_action()
    elif args.action == "migration19-test":
        migration19_test_action()
    elif args.action == "isolated-e2e":
        isolated_e2e_action()
    elif args.action == "live-discovery":
        live_discovery_action()
    elif args.action == "cleanup-isolated":
        cleanup_isolated_action()
    elif args.action == "deploy":
        deploy_action()
    elif args.action == "formal-verify":
        formal_verify_action()
    elif args.action == "launcher-gate":
        launcher_gate_action()
    elif args.action == "migration-diagnose":
        migration_diagnose_action()
    elif args.action == "capture-index-files":
        capture_index_files_action()
    elif args.action == "upload-reports":
        upload_reports_action()
    elif args.action == "commit":
        commit_action()
    elif args.action == "commit-diagnose":
        commit_diagnose_action()
    elif args.action == "finalize-runtime":
        finalize_runtime_action()


if __name__ == "__main__":
    main()
