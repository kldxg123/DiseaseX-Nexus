from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shlex
import stat
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import a100_p1_9 as p19


LOCAL_ROOT = Path(__file__).resolve().parents[1]
BASE_ROOT = LOCAL_ROOT / "remote_edit" / "p1_10_base"
WORK_ROOT = LOCAL_ROOT / "remote_edit" / "p1_10_work"
GATE_ROOT = LOCAL_ROOT / "remote_edit" / "p1_10_gate"
HEAD_ROOT = LOCAL_ROOT / "remote_edit" / "p1_10_head"
COMMIT_OVERRIDE_ROOT = LOCAL_ROOT / "remote_edit" / "p1_10_commit_override"
PRESERVE_DIRTY_ROOT = LOCAL_ROOT / "remote_edit" / "p1_10_preserve_dirty"
BASE_FILES = (
    "alembic.ini",
    "pyproject.toml",
    "migrations/env.py",
    "migrations/versions/0010_p1_8_daily_updates.py",
    "migrations/versions/0017_p1_9_crypto_resolution.py",
    "src/epidemic_core/db/models/tables.py",
    "src/epidemic_core/discovery/review_app.py",
    "src/epidemic_core/discovery/review_main.py",
    "src/epidemic_core/discovery/candidate_pipeline.py",
    "src/epidemic_core/discovery/worker.py",
    "src/epidemic_core/dashboard/app.py",
    "src/epidemic_core/dashboard/static/dashboard.p1-7ar-v1.css",
    "src/epidemic_core/dashboard/static/dashboard.p1-7ar-v1.js",
    "src/epidemic_core/p1_9/promotion.py",
    "src/epidemic_core/p1_9/review_app.py",
    "src/epidemic_core/p1_9/dashboard_app.py",
    "scripts/source_review/install_runtime.sh",
    "scripts/source_review/start_source_review.sh",
    "scripts/dashboard/install_runtime.sh",
    "scripts/dashboard/start_dashboard.sh",
    "scripts/dashboard/_common.sh",
    "scripts/daily_update/_common.sh",
    "scripts/daily_update/start_daily_update.sh",
    "scripts/source_discovery_worker/start_source_discovery_worker.sh",
    "scripts/source_promotion_worker/_common.sh",
    "scripts/source_promotion_worker/start_source_promotion_worker.sh",
    "tests/unit/test_dashboard.py",
    "tests/unit/test_p1_9_promotion.py",
    "tests/unit/test_p1_9_ui.py",
)
P1_10_FILES = (
    "migrations/versions/0018_p1_10_preview_publication.py",
    "src/epidemic_core/p1_10/__init__.py",
    "src/epidemic_core/p1_10/preview.py",
    "src/epidemic_core/p1_10/review_app.py",
    "src/epidemic_core/p1_10/dashboard_app.py",
    "src/epidemic_core/discovery/candidate_pipeline.py",
    "src/epidemic_core/discovery/worker.py",
    "src/epidemic_core/discovery/review_app.py",
    "src/epidemic_core/discovery/static/review.p1-10-preview.css",
    "src/epidemic_core/dashboard/app.py",
    "src/epidemic_core/dashboard/static/dashboard.p1-10-preview.css",
    "src/epidemic_core/dashboard/static/dashboard.p1-10-preview.js",
    "scripts/preview_publication_worker/_common.sh",
    "scripts/preview_publication_worker/start_preview_publication_worker.sh",
    "scripts/preview_publication_worker/status_preview_publication_worker.sh",
    "scripts/preview_publication_worker/stop_preview_publication_worker.sh",
    "scripts/source_review/install_runtime.sh",
    "scripts/dashboard/_common.sh",
    "scripts/dashboard/install_runtime.sh",
    "tests/unit/test_p1_10_preview.py",
)
REPORT_ROOT = LOCAL_ROOT / "reports" / "p1_10_formal_preview"


def q(value: str) -> str:
    return shlex.quote(value)


def _remote_json(client: Any, python: str, setup: str, code: str, timeout: int = 600) -> Any:
    command = f"{setup}; exec {q(python)} -B -c {q(code)}"
    return json.loads(p19.a100.checked(client, f"bash -c {q(command)}", timeout=timeout))


def snapshot_action() -> None:
    client, gate = p19.a100.connect()
    try:
        root, project = p19.a100.paths(client)
        if p19.a100.checked(client, f"git -C {q(project)} rev-parse HEAD") != "96de1b09a317dbd614744521cca4ef833e556f65":
            raise SystemExit("P1_10_UNEXPECTED_HEAD")
        for directory in (BASE_ROOT, WORK_ROOT, GATE_ROOT):
            directory.mkdir(parents=True, exist_ok=True)
        sftp = client.open_sftp()
        manifest: dict[str, Any] = {}
        try:
            for relative in BASE_FILES:
                remote_path = project + "/" + relative
                try:
                    attrs = sftp.stat(remote_path)
                    with sftp.file(remote_path, "rb") as handle:
                        payload = handle.read()
                except OSError:
                    manifest[relative] = {"exists": False}
                    continue
                for destination in (BASE_ROOT / relative, WORK_ROOT / relative):
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(payload)
                manifest[relative] = {
                    "exists": True,
                    "bytes": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "mode": stat.S_IMODE(attrs.st_mode),
                }
        finally:
            sftp.close()
        status = p19.a100.checked(client, f"git -C {q(project)} status --porcelain=v2 -uall")
        unstaged = p19.a100.checked(client, f"git -C {q(project)} diff --no-ext-diff --binary")
        home = p19.a100.checked(client, "printf '%s' \"$HOME\"")
        redacted = unstaged.replace(home, "[current-user-home]")
        if re.search(r"(?i)(password|token|secret)(\s*[=:]\s*)\S+", redacted):
            redacted = re.sub(
                r"(?i)(password|token|secret)(\s*[=:]\s*)\S+",
                r"\1\2[redacted]",
                redacted,
            )
        (GATE_ROOT / "status_porcelain_v2.txt").write_text(status, encoding="utf-8")
        (GATE_ROOT / "unstaged_redacted.diff").write_text(redacted, encoding="utf-8")
        (GATE_ROOT / "staged.diff").write_text("", encoding="utf-8")
        metadata = {
            "branch": p19.a100.checked(client, f"git -C {q(project)} branch --show-current"),
            "head": p19.a100.checked(client, f"git -C {q(project)} rev-parse HEAD"),
            "status_count": len(status.splitlines()),
            "status_sha256": hashlib.sha256(status.encode()).hexdigest(),
            "unstaged_sha256": hashlib.sha256(unstaged.encode()).hexdigest(),
            "staged_sha256": hashlib.sha256(b"").hexdigest(),
            "untracked_count": sum(1 for line in status.splitlines() if line.startswith("? ")),
            "files": manifest,
            "host_key_type": gate.get("key_type"),
            "host_key_fingerprint_recorded": bool(gate.get("fingerprint")),
        }
        (GATE_ROOT / "git_gate.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
        print(json.dumps({key: metadata[key] for key in ("branch", "head", "status_count", "status_sha256", "unstaged_sha256", "untracked_count")}, indent=2))
    finally:
        client.close()


def baseline_action() -> None:
    client, _gate = p19.a100.connect()
    try:
        root, project = p19.a100.paths(client)
        python = root + "/conda/envs/epidemic-core/bin/python"
        code = r'''import hashlib,json,os,pathlib,pwd,subprocess
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import make_url
url=make_url(os.environ["MIGRATION_DATABASE_URL"]).set(username="epidemicdb",password=os.environ["POSTGRES_ADMIN_PASSWORD"])
engine=sa.create_engine(url,hide_parameters=True)
tables=("data_sources","query_requests","retrieval_runs","source_fetches","raw_artifacts","normalized_observations","quality_assessments","dataset_snapshots","dataset_snapshot_observations")
with engine.connect() as db:
 counts={name:db.execute(text('SELECT count(*) FROM epidemic.'+name)).scalar_one() for name in tables}
 snapshots=[dict(r) for r in db.execute(text("SELECT id,row_count,content_sha256,created_at FROM epidemic.dataset_snapshots ORDER BY created_at,id")).mappings()]
 migration=db.execute(text("SELECT version_num FROM epidemic.alembic_version")).scalar_one()
 daily=[dict(r) for r in db.execute(text("SELECT schedule_id,schedule_name,enabled,data_source_id,query_request_id,next_due_at,last_success_at,consecutive_failures,state_version FROM epidemic.daily_update_schedules ORDER BY schedule_name")).mappings()]
 training_count=db.execute(text("SELECT count(*) FROM epidemic.training_snapshot_observations_v1")).scalar_one()
 candidate=dict(db.execute(text("""SELECT c.id candidate_id,c.status,cv.id candidate_version_id,rc.id review_case_id,rc.state,ar.sha256 artifact_sha256,sp.schema_sha256,mp.mapping_sha256,cv.terms_sha256,cv.license_status FROM epidemic_discovery.source_candidates c JOIN epidemic_discovery.source_candidate_versions cv ON cv.candidate_id=c.id JOIN epidemic_discovery.source_review_cases rc ON rc.candidate_version_id=cv.id JOIN epidemic_discovery.quarantine_artifacts ar ON ar.candidate_version_id=cv.id JOIN epidemic_discovery.source_schema_profiles sp ON sp.candidate_version_id=cv.id JOIN epidemic_discovery.source_mapping_proposals mp ON mp.candidate_version_id=cv.id AND mp.revision=rc.current_mapping_revision WHERE c.publisher ILIKE '%Brazil%' OR c.publisher ILIKE '%DATASUS%' ORDER BY cv.created_at DESC LIMIT 1""")).mappings().one())
 trusted_decisions=db.execute(text("SELECT count(*) FROM epidemic_discovery.source_review_decisions WHERE review_case_id=:case"),{"case":candidate["review_case_id"]}).scalar_one()
 trusted_promotions=db.execute(text("SELECT count(*) FROM epidemic_discovery.source_promotion_runs p JOIN epidemic_discovery.source_review_decisions d ON d.id=p.review_decision_id WHERE d.review_case_id=:case"),{"case":candidate["review_case_id"]}).scalar_one()
 preview_schema=db.execute(text("SELECT count(*) FROM information_schema.schemata WHERE schema_name='epidemic_preview'")).scalar_one()
 p19_active=db.execute(text("SELECT count(*) FROM epidemic_discovery.p1_9_publications")).scalar_one()
 roles=[dict(r) for r in db.execute(text("SELECT rolname,rolsuper,rolcreaterole,rolcreatedb FROM pg_roles WHERE rolname IN ('epidemic_app','epidemic_dashboard_ro','epidemicreview','epidemicretrieval','epidemicnormalizer','epidemicdb') ORDER BY rolname")).mappings()]
 def stable_snapshot(row):
  payload={"id":str(row["id"]),"row_count":row["row_count"],"sha256":row["content_sha256"]}
  return payload
 result={"counts":counts,"snapshots":[stable_snapshot(r) for r in snapshots],"migration":migration,"daily_schedules":daily,"daily_schedule_count":len(daily),"training_view_count":training_count,"candidate":candidate,"trusted_decision_count":trusted_decisions,"trusted_promotion_count":trusted_promotions,"preview_schema_count":preview_schema,"p1_9_active_formal_publications":p19_active,"roles":roles}
print(json.dumps(result,default=str))
'''
        setup = (
            f"source {q(project + '/scripts/database/_env.sh')}; set -a; "
            f"source {q(project + '/.secrets/postgres.env')}; set +a"
        )
        database = _remote_json(client, python, setup, code)
        services = {}
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
        for name, relative in service_paths.items():
            rc, _out, _err = p19.a100.run(client, q(project + "/" + relative), timeout=180)
            services[name] = rc == 0
        process_code = r'''import json,os,pathlib,pwd,socket
project=os.environ["PROJECT"].encode();rows=[]
for item in pathlib.Path("/proc").iterdir():
 if not item.name.isdigit():continue
 try:cmd=(item/"cmdline").read_bytes();status=(item/"status").read_text();env=(item/"environ").read_bytes()
 except Exception:continue
 if project not in cmd:continue
 uid=int(next(line.split()[1] for line in status.splitlines() if line.startswith("Uid:")))
 names={x.split(b"=",1)[0].decode(errors="replace") for x in env.split(b"\0") if b"=" in x}
 rows.append({"pid":int(item.name),"uid":uid,"current_uid":os.getuid(),"same_uid":uid==os.getuid(),"proxy_variables_present":bool(names&{"http_proxy","https_proxy","HTTP_PROXY","HTTPS_PROXY","all_proxy","ALL_PROXY"})})
listeners=[]
for filename,v6 in (("/proc/net/tcp",False),("/proc/net/tcp6",True)):
 for line in pathlib.Path(filename).read_text().splitlines()[1:]:
  fields=line.split();addr,port_hex=fields[1].split(":");port=int(port_hex,16)
  if fields[3]=="0A" and port in {5432,8000,8010,8020,8021,18789}:
   listeners.append({"port":port,"loopback":addr==("00000000000000000000000001000000" if v6 else "0100007F")})
print(json.dumps({"processes":rows,"listeners":listeners}))
'''
        runtime = _remote_json(client, python, f"export PROJECT={q(project)}", process_code)
        cli = project + "/scripts/openclaw/openclaw_cli.sh"
        version = p19.a100.checked(client, f"{q(cli)} --version")
        cron_raw = p19.a100.checked(client, f"{q(cli)} cron list --json", timeout=300)
        try:
            cron = json.loads(cron_raw)
        except json.JSONDecodeError:
            cron = {"parsed": False, "output_sha256": hashlib.sha256(cron_raw.encode()).hexdigest()}
        tool_rc, tool_out, tool_err = p19.a100.run(client, f"{q(cli)} tools list --json", timeout=300)
        plugins_rc, plugins_out, plugins_err = p19.a100.run(client, f"{q(cli)} plugins list --json", timeout=300)
        openclaw = {
            "version": version,
            "cron": cron,
            "tools_command_returncode": tool_rc,
            "tools_output_sha256": hashlib.sha256((tool_out + tool_err).encode()).hexdigest(),
            "plugins_command_returncode": plugins_rc,
            "plugins_output_sha256": hashlib.sha256((plugins_out + plugins_err).encode()).hexdigest(),
        }
        result = {
            "database": database,
            "services": services,
            "runtime": runtime,
            "openclaw": openclaw,
            "git": {
                "branch": p19.a100.checked(client, f"git -C {q(project)} branch --show-current"),
                "head": p19.a100.checked(client, f"git -C {q(project)} rev-parse HEAD"),
                "dirty_count": len(p19.a100.checked(client, f"git -C {q(project)} status --porcelain=v2 -uall").splitlines()),
                "staged_count": len(p19.a100.checked(client, f"git -C {q(project)} diff --cached --name-only").splitlines()),
            },
        }
        print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
    finally:
        client.close()


def upload_action() -> None:
    client, _gate = p19.a100.connect()
    try:
        _root, project = p19.a100.paths(client)
        if p19.a100.checked(client, f"git -C {q(project)} rev-parse HEAD") != "96de1b09a317dbd614744521cca4ef833e556f65":
            raise SystemExit("P1_10_UNEXPECTED_HEAD")
        if p19.a100.checked(client, f"git -C {q(project)} diff --cached --name-only"):
            raise SystemExit("P1_10_INDEX_NOT_EMPTY")
        sftp = client.open_sftp()
        uploaded: list[str] = []
        try:
            for relative in P1_10_FILES:
                source = WORK_ROOT / relative
                if not source.is_file():
                    raise FileNotFoundError(relative)
                remote_path = project + "/" + relative
                p19.a100.checked(client, f"mkdir -p {q(remote_path.rsplit('/', 1)[0])}")
                with sftp.file(remote_path, "wb") as handle:
                    handle.write(source.read_bytes())
                mode = 0o755 if relative.startswith("scripts/") else 0o644
                sftp.chmod(remote_path, mode)
                uploaded.append(relative)
        finally:
            sftp.close()
        print(json.dumps({"uploaded_count": len(uploaded)}))
    finally:
        client.close()


def upload_reports_action() -> None:
    client, _gate = p19.a100.connect()
    try:
        _root, project = p19.a100.paths(client)
        report_files = sorted(path for path in REPORT_ROOT.iterdir() if path.is_file())
        if len(report_files) != 23:
            raise SystemExit(f"P1_10_REPORT_FILE_COUNT:{len(report_files)}")
        remote_root = project + "/reports/p1_10_formal_preview"
        p19.a100.checked(client, f"mkdir -p {q(remote_root)}")
        sftp = client.open_sftp()
        uploaded: list[str] = []
        try:
            for source in report_files:
                remote_path = remote_root + "/" + source.name
                with sftp.file(remote_path, "wb") as handle:
                    handle.write(source.read_bytes())
                sftp.chmod(remote_path, 0o644)
                uploaded.append(source.name)
        finally:
            sftp.close()
        print(json.dumps({"uploaded_count": len(uploaded), "secret_values_exposed": False}))
    finally:
        client.close()


def download_head_action() -> None:
    client, _gate = p19.a100.connect()
    try:
        _root, project = p19.a100.paths(client)
        HEAD_ROOT.mkdir(parents=True, exist_ok=True)
        sftp = client.open_sftp()
        downloaded = 0
        try:
            for relative in P1_10_FILES:
                rc, _out, _err = p19.a100.run(client, f"git -C {q(project)} cat-file -e {q('HEAD:' + relative)}", timeout=120)
                if rc != 0:
                    continue
                temporary = project + "/storage/p1_10_head_export_" + secrets.token_hex(6)
                command = f"git -C {q(project)} show {q('HEAD:' + relative)} > {q(temporary)}"
                p19.a100.checked(client, f"bash -c {q(command)}", timeout=120)
                target = HEAD_ROOT / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                with sftp.file(temporary, "rb") as handle:
                    target.write_bytes(handle.read())
                p19.a100.run(client, f"python3 -c {q('import pathlib,sys;pathlib.Path(sys.argv[1]).unlink(missing_ok=True)')} {q(temporary)}", timeout=120)
                downloaded += 1
        finally:
            sftp.close()
        print(json.dumps({"downloaded": downloaded, "secret_values_exposed": False}))
    finally:
        client.close()


def commit_action() -> None:
    client, _gate = p19.a100.connect()
    temporary_root = ""
    try:
        _root, project = p19.a100.paths(client)
        expected_head = "96de1b09a317dbd614744521cca4ef833e556f65"
        if p19.a100.checked(client, f"git -C {q(project)} rev-parse HEAD") != expected_head:
            raise SystemExit("P1_10_UNEXPECTED_HEAD")
        if p19.a100.checked(client, f"git -C {q(project)} diff --cached --name-only"):
            raise SystemExit("P1_10_INDEX_NOT_EMPTY")
        report_files = sorted(path for path in REPORT_ROOT.iterdir() if path.is_file())
        relative_reports = tuple(f"reports/p1_10_formal_preview/{path.name}" for path in report_files)
        all_paths = tuple(P1_10_FILES) + relative_reports
        if len(all_paths) != 43 or len(set(all_paths)) != len(all_paths):
            raise SystemExit("P1_10_COMMIT_PATH_INVENTORY_INVALID")
        baseline_lines = (GATE_ROOT / "status_porcelain_v2.txt").read_text(encoding="utf-8").splitlines()

        def status_path(line: str) -> str:
            if line.startswith("? "):
                return line[2:]
            if line.startswith("1 "):
                return line.split(" ", 8)[-1]
            if "\t" in line:
                return line.rsplit("\t", 1)[-1]
            return line.split()[-1]

        baseline_paths = {status_path(line) for line in baseline_lines if line}
        if len(baseline_paths) != 51:
            raise SystemExit("P1_10_BASELINE_PATH_COUNT_INVALID")
        token = secrets.token_hex(8)
        temporary_root = project + "/storage/p1_10_commit/" + token
        p19.a100.checked(client, f"mkdir -p {q(temporary_root + '/base')} {q(temporary_root + '/work')} {q(temporary_root + '/result')}")
        p19.a100.checked(client, f"chmod 700 {q(temporary_root)}")
        sftp = client.open_sftp()
        try:
            for relative in all_paths:
                if relative in P1_10_FILES:
                    base_source = BASE_ROOT / relative
                    override_source = COMMIT_OVERRIDE_ROOT / relative
                    work_source = override_source if override_source.is_file() else WORK_ROOT / relative
                else:
                    base_source = Path("__missing__")
                    work_source = REPORT_ROOT / Path(relative).name
                if not work_source.is_file():
                    raise FileNotFoundError(relative)
                for bucket, source in (("base", base_source), ("work", work_source)):
                    if not source.is_file():
                        continue
                    remote_path = temporary_root + f"/{bucket}/" + relative
                    p19.a100.checked(client, f"mkdir -p {q(remote_path.rsplit('/', 1)[0])}")
                    with sftp.file(remote_path, "wb") as handle:
                        handle.write(source.read_bytes())
                    sftp.chmod(remote_path, 0o600)
        finally:
            sftp.close()
        shell_inputs = [temporary_root + "/work/" + relative for relative in all_paths if relative.endswith(".sh")]
        if shell_inputs:
            p19.a100.checked(client, "bash -n " + " ".join(q(path) for path in shell_inputs), timeout=180)
        index_path = temporary_root + "/index"
        p19.a100.checked(client, f"GIT_INDEX_FILE={q(index_path)} git -C {q(project)} read-tree HEAD")
        merged_paths: list[str] = []
        for relative in all_paths:
            base_path = temporary_root + "/base/" + relative
            work_path = temporary_root + "/work/" + relative
            result_path = temporary_root + "/result/" + relative
            p19.a100.checked(client, f"mkdir -p {q(result_path.rsplit('/', 1)[0])}")
            head_rc, _head_out, _head_err = p19.a100.run(client, f"git -C {q(project)} cat-file -e {q('HEAD:' + relative)}", timeout=120)
            base_rc, _base_out, _base_err = p19.a100.run(client, f"test -f {q(base_path)}", timeout=120)
            manually_resolved = (COMMIT_OVERRIDE_ROOT / relative).is_file()
            if manually_resolved:
                p19.a100.checked(client, f"cp {q(work_path)} {q(result_path)}", timeout=120)
            elif head_rc == 0 and base_rc == 0:
                head_path = temporary_root + "/head/" + relative
                p19.a100.checked(client, f"mkdir -p {q(head_path.rsplit('/', 1)[0])}")
                command = f"git -C {q(project)} show {q('HEAD:' + relative)} > {q(head_path)}"
                p19.a100.checked(client, f"bash -c {q(command)}", timeout=120)
                merge_command = f"git merge-file -p {q(head_path)} {q(base_path)} {q(work_path)} > {q(result_path)}"
                rc, _out, error = p19.a100.run(client, f"bash -c {q(merge_command)}", timeout=120)
                if rc != 0:
                    raise RuntimeError(f"P1_10_MERGE_CONFLICT:{relative}:{error[-120:]}")
            elif head_rc != 0 and base_rc != 0:
                p19.a100.checked(client, f"cp {q(work_path)} {q(result_path)}", timeout=120)
            else:
                raise RuntimeError(f"P1_10_UNSAFE_FILE_HISTORY:{relative}")
            blob = p19.a100.checked(client, f"git -C {q(project)} hash-object -w {q(result_path)}")
            if head_rc == 0:
                tree_line = p19.a100.checked(client, f"git -C {q(project)} ls-tree HEAD -- {q(relative)}")
                mode = tree_line.split()[0]
            else:
                mode = "100755" if relative.startswith("scripts/") and relative.endswith(".sh") else "100644"
            update = f"GIT_INDEX_FILE={q(index_path)} git -C {q(project)} update-index --add --cacheinfo {q(mode + ',' + blob + ',' + relative)}"
            p19.a100.checked(client, f"bash -c {q(update)}", timeout=120)
            merged_paths.append(relative)
        tree = p19.a100.checked(client, f"GIT_INDEX_FILE={q(index_path)} git -C {q(project)} write-tree")
        subject = "feat(p1-10): add formal candidate preview publication tier"
        new_commit = p19.a100.checked(client, f"git -C {q(project)} commit-tree {q(tree)} -p {q(expected_head)} -m {q(subject)}")
        p19.a100.checked(client, f"git -C {q(project)} update-ref refs/heads/main {q(new_commit)} {q(expected_head)}")
        committed_paths = set(p19.a100.checked(client, f"git -C {q(project)} diff-tree --no-commit-id --name-only -r {q(new_commit)}").splitlines())
        if committed_paths != set(all_paths):
            raise RuntimeError("P1_10_COMMITTED_PATH_SET_MISMATCH")
        if p19.a100.checked(client, f"git -C {q(project)} diff --cached --name-only"):
            raise RuntimeError("P1_10_REAL_INDEX_DIRTY_AFTER_COMMIT")
        preserved_sources = sorted(path for path in PRESERVE_DIRTY_ROOT.rglob("*") if path.is_file())
        if preserved_sources:
            sftp = client.open_sftp()
            try:
                for source in preserved_sources:
                    relative = source.relative_to(PRESERVE_DIRTY_ROOT).as_posix()
                    if relative not in baseline_paths:
                        raise RuntimeError(f"P1_10_PRESERVE_PATH_NOT_IN_BASELINE:{relative}")
                    remote_path = project + "/" + relative
                    with sftp.file(remote_path, "wb") as handle:
                        handle.write(source.read_bytes())
                    sftp.chmod(remote_path, 0o755 if relative.startswith("scripts/") else 0o644)
            finally:
                sftp.close()
        final_status = p19.a100.checked(client, f"git -C {q(project)} status --porcelain=v2 -uall")
        final_paths = {status_path(line) for line in final_status.splitlines() if line}
        if final_paths != baseline_paths:
            missing = sorted(baseline_paths - final_paths)
            extra = sorted(final_paths - baseline_paths)
            raise RuntimeError(f"P1_10_PREEXISTING_DIRTY_SET_CHANGED:missing={missing}:extra={extra}")
        print(json.dumps({"commit": new_commit, "subject": subject, "committed_paths": len(committed_paths), "staged_paths": 0, "preexisting_dirty_paths": len(final_paths), "push": False}, indent=2))
    finally:
        if temporary_root:
            cleanup_code = "import pathlib,shutil,sys;p=pathlib.Path(sys.argv[1]);assert p.name and p.parent.name=='p1_10_commit';shutil.rmtree(p,ignore_errors=True)"
            p19.a100.run(client, f"python3 -c {q(cleanup_code)} {q(temporary_root)}", timeout=120)
        client.close()


def repair_index_action() -> None:
    client, _gate = p19.a100.connect()
    try:
        _root, project = p19.a100.paths(client)
        expected_parent = "96de1b09a317dbd614744521cca4ef833e556f65"
        head = p19.a100.checked(client, f"git -C {q(project)} rev-parse HEAD")
        parent = p19.a100.checked(client, f"git -C {q(project)} rev-parse HEAD^")
        subject = p19.a100.checked(client, f"git -C {q(project)} show -s --format=%s HEAD")
        if parent != expected_parent or subject != "feat(p1-10): add formal candidate preview publication tier":
            raise SystemExit("P1_10_REPAIR_UNEXPECTED_COMMIT")
        report_files = sorted(path for path in REPORT_ROOT.iterdir() if path.is_file())
        all_paths = tuple(P1_10_FILES) + tuple(f"reports/p1_10_formal_preview/{path.name}" for path in report_files)
        committed_paths = set(p19.a100.checked(client, f"git -C {q(project)} diff-tree --no-commit-id --name-only -r HEAD").splitlines())
        staged_paths = set(p19.a100.checked(client, f"git -C {q(project)} diff --cached --name-only").splitlines())
        if committed_paths != set(all_paths) or staged_paths != set(all_paths):
            raise SystemExit("P1_10_REPAIR_PATH_SET_MISMATCH")
        for relative in all_paths:
            tree_line = p19.a100.checked(client, f"git -C {q(project)} ls-tree HEAD -- {q(relative)}")
            fields = tree_line.split()
            mode, blob = fields[0], fields[2]
            p19.a100.checked(client, f"git -C {q(project)} update-index --add --cacheinfo {q(mode + ',' + blob + ',' + relative)}")
        if p19.a100.checked(client, f"git -C {q(project)} diff --cached --name-only"):
            raise RuntimeError("P1_10_REPAIR_INDEX_NOT_EMPTY")
        preserved_sources = sorted(path for path in PRESERVE_DIRTY_ROOT.rglob("*") if path.is_file())
        sftp = client.open_sftp()
        try:
            for source in preserved_sources:
                relative = source.relative_to(PRESERVE_DIRTY_ROOT).as_posix()
                remote_path = project + "/" + relative
                with sftp.file(remote_path, "wb") as handle:
                    handle.write(source.read_bytes())
                sftp.chmod(remote_path, 0o755 if relative.startswith("scripts/") else 0o644)
        finally:
            sftp.close()
        baseline_lines = (GATE_ROOT / "status_porcelain_v2.txt").read_text(encoding="utf-8").splitlines()
        def status_path(line: str) -> str:
            if line.startswith("? "):
                return line[2:]
            if line.startswith("1 "):
                return line.split(" ", 8)[-1]
            if "\t" in line:
                return line.rsplit("\t", 1)[-1]
            return line.split()[-1]
        baseline_paths = {status_path(line) for line in baseline_lines if line}
        final_status = p19.a100.checked(client, f"git -C {q(project)} status --porcelain=v2 -uall")
        final_paths = {status_path(line) for line in final_status.splitlines() if line}
        if final_paths != baseline_paths:
            raise RuntimeError(f"P1_10_REPAIR_DIRTY_SET_MISMATCH:missing={sorted(baseline_paths-final_paths)}:extra={sorted(final_paths-baseline_paths)}")
        print(json.dumps({"commit": head, "staged_paths": 0, "preexisting_dirty_paths": len(final_paths), "repaired": True}, indent=2))
    finally:
        client.close()


def validate_action() -> None:
    client, _gate = p19.a100.connect()
    try:
        root, project = p19.a100.paths(client)
        python = root + "/conda/envs/epidemic-core/bin/python"
        targets = (
            "migrations/versions/0018_p1_10_preview_publication.py src/epidemic_core/p1_10 "
            "src/epidemic_core/discovery/candidate_pipeline.py src/epidemic_core/discovery/worker.py src/epidemic_core/discovery/review_app.py src/epidemic_core/dashboard/app.py "
            "tests/unit/test_p1_10_preview.py"
        )
        commands = {
            "bash_syntax": "bash -n scripts/preview_publication_worker/*.sh scripts/source_review/install_runtime.sh scripts/dashboard/_common.sh scripts/dashboard/install_runtime.sh",
            "ruff": f"{q(python)} -m ruff check {targets}",
            "format": f"{q(python)} -m ruff format --check {targets}",
            "pytest": f"PYTHONPATH=src {q(python)} -m pytest -q -p no:cacheprovider tests/unit/test_p1_10_preview.py",
            "mypy": f"{q(python)} -m mypy --strict src/epidemic_core/p1_10",
            "alembic_heads": f"{q(python)} -m alembic heads",
        }
        results: dict[str, Any] = {}
        for name, command in commands.items():
            rc, output, error = p19.a100.run(client, f"cd {q(project)} && {command}", timeout=900)
            results[name] = {"returncode": rc, "tail": (output + error).splitlines()[-30:]}
        print(json.dumps(results, ensure_ascii=True, indent=2, sort_keys=True))
        if any(item["returncode"] != 0 for item in results.values()):
            raise SystemExit(1)
    finally:
        client.close()


def format_action() -> None:
    client, _gate = p19.a100.connect()
    try:
        root, project = p19.a100.paths(client)
        python = root + "/conda/envs/epidemic-core/bin/python"
        targets = (
            "migrations/versions/0018_p1_10_preview_publication.py src/epidemic_core/p1_10 "
            "src/epidemic_core/discovery/candidate_pipeline.py src/epidemic_core/discovery/worker.py src/epidemic_core/discovery/review_app.py src/epidemic_core/dashboard/app.py "
            "tests/unit/test_p1_10_preview.py"
        )
        p19.a100.checked(client, f"cd {q(project)} && {q(python)} -m ruff check --fix {targets}", timeout=600)
        p19.a100.checked(client, f"cd {q(project)} && {q(python)} -m ruff format {targets}", timeout=600)
        sftp = client.open_sftp()
        copied = 0
        try:
            for relative in P1_10_FILES:
                if not relative.endswith(".py"):
                    continue
                target = WORK_ROOT / relative
                with sftp.file(project + "/" + relative, "rb") as handle:
                    target.write_bytes(handle.read())
                copied += 1
        finally:
            sftp.close()
        print(json.dumps({"formatted_and_synced": copied}))
    finally:
        client.close()


def migration_test_action() -> None:
    client, _gate = p19.a100.connect()
    try:
        root, project = p19.a100.paths(client)
        python = root + "/conda/envs/epidemic-core/bin/python"
        code = r'''import hashlib,json,os,pathlib,secrets,subprocess,sys
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import make_url
project=pathlib.Path(os.environ["PROJECT"]);python=os.environ["PYTHON_BIN"];pgbin=pathlib.Path(os.environ["POSTGRES_BIN"])
formal=make_url(os.environ["MIGRATION_DATABASE_URL"]).set(username="epidemicdb",password=os.environ["POSTGRES_ADMIN_PASSWORD"]);admin=formal.set(database="postgres")
name="epidemic_p1_10_restore_"+secrets.token_hex(5);backup_dir=project/"storage/backups/p1_10"/secrets.token_hex(6);backup_dir.mkdir(parents=True,mode=0o700);dump=backup_dir/"pre_migration.dump"
env=os.environ.copy();env["PGPASSWORD"]=formal.password or "";checks=[]
def run(label,args,timeout=1800,extra_env=None):
 e=env.copy();e.update(extra_env or {});p=subprocess.run(args,cwd=project,env=e,text=True,capture_output=True,timeout=timeout)
 safe=(p.stdout+"\n"+p.stderr).replace(str(project),"[project]")
 checks.append({"label":label,"returncode":p.returncode,"output_sha256":hashlib.sha256(safe.encode()).hexdigest(),"tail":safe.splitlines()[-15:]})
 if p.returncode:
  low=p.stderr.lower();category="permission" if "permission denied" in low else "connection" if "connection" in low else "path" if "no such file" in low else "other"
  raise RuntimeError(label+" failed category="+category+" stderr_sha256="+hashlib.sha256(p.stderr.encode()).hexdigest())
 return p
def pg_args(database):return ["-h",formal.host or "127.0.0.1","-p",str(formal.port or 5432),"-U",formal.username or "epidemicdb","-d",database]
run("pg_dump",[str(pgbin/"pg_dump"),*pg_args(formal.database or "epidemic_openclaw"),"--format=custom","--no-owner","--no-privileges","--file",str(dump)])
os.chmod(dump,0o600);dump_sha=hashlib.sha256(dump.read_bytes()).hexdigest()
admin_engine=sa.create_engine(admin,isolation_level="AUTOCOMMIT",hide_parameters=True)
with admin_engine.connect() as db:
 db.execute(text("DO $$BEGIN IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='epidemicpreview') THEN CREATE ROLE epidemicpreview NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT; END IF; END$$"))
 db.exec_driver_sql('CREATE DATABASE "'+name+'" TEMPLATE template0 ENCODING \'UTF8\'')
try:
 run("pg_restore",[str(pgbin/"pg_restore"),*pg_args(name),"--exit-on-error","--no-owner","--no-privileges",str(dump)])
 test_url=formal.set(database=name);test_engine=sa.create_engine(test_url,hide_parameters=True)
 def inventory(engine):
  with engine.connect() as db:
   counts={t:db.execute(text('SELECT count(*) FROM epidemic.'+t)).scalar_one() for t in ("data_sources","query_requests","retrieval_runs","source_fetches","raw_artifacts","normalized_observations","quality_assessments","dataset_snapshots","dataset_snapshot_observations")}
   snaps=[dict(r) for r in db.execute(text("SELECT id,row_count,content_sha256 FROM epidemic.dataset_snapshots ORDER BY created_at,id")).mappings()]
   return {"counts":counts,"snapshots":snaps,"migration":db.execute(text("SELECT version_num FROM epidemic.alembic_version")).scalar_one()}
 source=inventory(sa.create_engine(formal,hide_parameters=True));restored=inventory(test_engine)
 if source!=restored:raise RuntimeError("RESTORE_INVENTORY_MISMATCH")
 alembic_env=env.copy();alembic_env["MIGRATION_DATABASE_URL"]=test_url.render_as_string(hide_password=False)
 run("alembic_upgrade",[python,"-m","alembic","upgrade","head"],900,alembic_env)
 with test_engine.connect() as db:
  upgraded={"migration":db.execute(text("SELECT version_num FROM epidemic.alembic_version")).scalar_one(),"preview_tables":db.execute(text("SELECT count(*) FROM information_schema.tables WHERE table_schema='epidemic_preview'")).scalar_one(),"preview_decisions":db.execute(text("SELECT count(*) FROM epidemic_preview.preview_review_decisions")).scalar_one(),"preview_publications":db.execute(text("SELECT count(*) FROM epidemic_preview.preview_dashboard_publications")).scalar_one(),"trusted_count":db.execute(text("SELECT count(*) FROM epidemic.normalized_observations")).scalar_one(),"trusted_dashboard_query":db.execute(text("SELECT count(*) FROM epidemic.dashboard_snapshot_catalog_v1")).scalar_one(),"empty_preview_query":db.execute(text("SELECT count(*) FROM epidemic_preview.active_preview_publications_v1")).scalar_one()}
  db.rollback();permission={}
  tests=(("preview_worker_trusted_write_denied","epidemicpreview","INSERT INTO epidemic.normalized_observations(id) VALUES(gen_random_uuid())"),("dashboard_raw_read_denied","epidemic_dashboard_ro","SELECT count(*) FROM epidemic_discovery.quarantine_artifacts"),("review_observation_write_denied","epidemicreview","INSERT INTO epidemic_preview.preview_observations(id) VALUES(gen_random_uuid())"),("daily_preview_read_denied","epidemic_app","SELECT count(*) FROM epidemic_preview.preview_observations"))
  for label,role,sql in tests:
   tx=db.begin()
   try:
    db.execute(text('SET LOCAL ROLE "'+role+'"'));db.execute(text(sql));permission[label]=False
   except Exception:permission[label]=True
   finally:tx.rollback()
 run("alembic_downgrade",[python,"-m","alembic","downgrade","0017_p1_9_crypto_resolution"],900,alembic_env)
 with test_engine.connect() as db:
  downgraded={"migration":db.execute(text("SELECT version_num FROM epidemic.alembic_version")).scalar_one(),"preview_schema":db.execute(text("SELECT count(*) FROM information_schema.schemata WHERE schema_name='epidemic_preview'")).scalar_one()}
 run("alembic_reupgrade",[python,"-m","alembic","upgrade","head"],900,alembic_env)
 run("alembic_heads",[python,"-m","alembic","heads"],300,alembic_env)
 final=inventory(test_engine);test_engine.dispose()
 if upgraded["migration"]!="0018_p1_10_preview_publication" or upgraded["preview_decisions"] or upgraded["preview_publications"] or upgraded["empty_preview_query"]:raise RuntimeError("PREVIEW_EMPTY_SCHEMA_GATE_FAILED")
 if downgraded!={"migration":"0017_p1_9_crypto_resolution","preview_schema":0}:raise RuntimeError("PREVIEW_DOWNGRADE_FAILED")
 if final["counts"]!=source["counts"] or final["snapshots"]!=source["snapshots"]:raise RuntimeError("TRUSTED_RESTORE_DRIFT")
 if not all(permission.values()):raise RuntimeError("PREVIEW_PERMISSION_DENIAL_FAILED")
 result={"backup_path":str(dump).replace(str(project),"[project]"),"backup_sha256":dump_sha,"backup_bytes":dump.stat().st_size,"restored_inventory_match":True,"source_inventory":source,"upgraded":upgraded,"downgraded":downgraded,"reupgraded_migration":final["migration"],"permissions":permission,"checks":checks,"pass":True}
 print(json.dumps(result,default=str))
finally:
 with admin_engine.connect() as db:
  db.execute(text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=:name"),{"name":name});db.exec_driver_sql('DROP DATABASE IF EXISTS "'+name+'"')
 admin_engine.dispose()
'''
        setup = (
            f"source {q(project + '/scripts/database/_env.sh')}; set -a; source {q(project + '/.secrets/postgres.env')}; set +a; "
            f"export PROJECT={q(project)} PYTHON_BIN={q(python)} POSTGRES_BIN={q(root + '/conda/envs/postgresql18/bin')}"
        )
        result = _remote_json(client, python, setup, code, timeout=5400)
        print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
    finally:
        client.close()


def prepare_demo_action() -> None:
    client, _gate = p19.a100.connect()
    try:
        root, project = p19.a100.paths(client)
        python = root + "/conda/envs/epidemic-core/bin/python"
        p19.a100.checked(client, q(project + "/scripts/p1_9_demo/start_demo.sh"), timeout=180)
        code = r'''import json,os,pathlib,secrets,subprocess,urllib.parse
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import make_url
project=pathlib.Path(os.environ["PROJECT"]);python=os.environ["PYTHON_BIN"]
admin_url=make_url(os.environ["DEMO_ADMIN_DATABASE_URL"])
env=os.environ.copy();env["MIGRATION_DATABASE_URL"]=admin_url.render_as_string(hide_password=False)
upgrade=subprocess.run([python,"-m","alembic","upgrade","head"],cwd=project,env=env,text=True,capture_output=True,timeout=900)
if upgrade.returncode:raise RuntimeError("P1_10_DEMO_MIGRATION_FAILED:"+upgrade.stderr[-300:])
preview_password=secrets.token_urlsafe(36)
engine=sa.create_engine(admin_url,hide_parameters=True)
with engine.begin() as db:
 migration=db.execute(text("SELECT version_num FROM epidemic.alembic_version")).scalar_one()
 if migration!="0018_p1_10_preview_publication":raise RuntimeError("P1_10_DEMO_MIGRATION_INVALID")
 db.execute(text("DELETE FROM epidemic_preview.environment_markers"))
 db.execute(text("INSERT INTO epidemic_preview.environment_markers(environment,database_name,production,disposable) VALUES('p1_10_test',current_database(),false,true)"))
 db.execute(text("SELECT set_config('p1_10.preview_password',:password,true)"),{"password":preview_password})
 db.exec_driver_sql("DO $$BEGIN IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='p1_10_demo_preview') THEN EXECUTE format('CREATE ROLE p1_10_demo_preview LOGIN PASSWORD %%L',current_setting('p1_10.preview_password')); END IF; END$$")
 db.exec_driver_sql('GRANT CONNECT ON DATABASE "epidemic_p1_9_demo" TO p1_10_demo_preview')
 db.exec_driver_sql('GRANT USAGE ON SCHEMA epidemic_preview,epidemic_discovery TO p1_10_demo_preview')
 db.exec_driver_sql('GRANT SELECT,INSERT,UPDATE ON ALL TABLES IN SCHEMA epidemic_preview TO p1_10_demo_preview')
 db.exec_driver_sql('GRANT DELETE ON epidemic_preview.preview_staging_observations TO p1_10_demo_preview')
 db.exec_driver_sql('GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA epidemic_preview TO p1_10_demo_preview')
 db.exec_driver_sql('GRANT SELECT ON epidemic_discovery.source_review_cases,epidemic_discovery.source_candidates,epidemic_discovery.source_candidate_versions,epidemic_discovery.quarantine_artifacts,epidemic_discovery.source_schema_profiles,epidemic_discovery.source_mapping_proposals,epidemic_discovery.source_quality_assessments,epidemic_discovery.discovery_runs,epidemic_discovery.source_discovery_search_results,epidemic_discovery.candidate_fetches,epidemic_discovery.discovery_run_candidate_links TO p1_10_demo_preview')
 db.exec_driver_sql('REVOKE ALL ON ALL TABLES IN SCHEMA epidemic FROM p1_10_demo_preview')
 db.exec_driver_sql('GRANT SELECT,INSERT ON epidemic_discovery.discovery_run_candidate_links TO p1_9_demo_app')
 db.exec_driver_sql('GRANT SELECT ON epidemic_discovery.discovery_run_candidate_links TO p1_9_demo_review')
 db.exec_driver_sql('GRANT USAGE ON SCHEMA epidemic_preview TO p1_9_demo_review')
 db.exec_driver_sql('GRANT SELECT ON epidemic_preview.preview_review_cards_v1,epidemic_preview.preview_review_decisions,epidemic_preview.preview_publication_jobs,epidemic_preview.preview_publication_events TO p1_9_demo_review')
 db.exec_driver_sql('GRANT EXECUTE ON FUNCTION epidemic_preview.submit_preview_decision(uuid,text,text,uuid,integer,text,jsonb,jsonb,char,jsonb,char,text,boolean) TO p1_9_demo_review')
 db.exec_driver_sql('GRANT EXECUTE ON FUNCTION epidemic_preview.queue_preview_publication(uuid,uuid) TO p1_9_demo_review')
 db.exec_driver_sql('GRANT EXECUTE ON FUNCTION epidemic_preview.revoke_preview_publication(uuid,text,text,uuid) TO p1_9_demo_review')
 db.exec_driver_sql('GRANT USAGE ON SCHEMA epidemic_preview TO p1_9_demo_dashboard')
 db.exec_driver_sql('GRANT SELECT ON epidemic_preview.active_preview_publications_v1,epidemic_preview.preview_dashboard_series_v1,epidemic_preview.preview_lineage_v1 TO p1_9_demo_dashboard')
 marker=db.execute(text("SELECT NOT production AND disposable AND database_name=current_database() FROM epidemic_preview.environment_markers WHERE environment='p1_10_test'")).scalar_one()
 preview_counts={name:db.execute(text('SELECT count(*) FROM epidemic_preview.'+name)).scalar_one() for name in ('preview_review_decisions','preview_publication_jobs','preview_dashboard_publications')}
 p19_counts={"decisions":db.execute(text("SELECT count(*) FROM epidemic_discovery.source_review_decisions")).scalar_one(),"approvals":db.execute(text("SELECT count(*) FROM epidemic_discovery.source_review_decisions WHERE decision='APPROVED'")).scalar_one(),"promotions":db.execute(text("SELECT count(*) FROM epidemic_discovery.source_promotion_runs")).scalar_one()}
 if not marker or any(preview_counts.values()) or p19_counts["approvals"] or p19_counts["promotions"]:raise RuntimeError("P1_10_DEMO_EMPTY_GATE_FAILED:"+json.dumps({"preview":preview_counts,"p1_9":p19_counts,"marker":marker}))
def role_url():
 return sa.URL.create("postgresql+psycopg",username="p1_10_demo_preview",password=preview_password,host="127.0.0.1",port=15432,database="epidemic_p1_9_demo").render_as_string(hide_password=False)
secret=project/".secrets/p1_9_demo.env";lines=[line for line in secret.read_text().splitlines() if not line.startswith("DEMO_PREVIEW_DATABASE_URL=")]
lines.append("DEMO_PREVIEW_DATABASE_URL="+urllib.parse.quote(role_url(),safe=":/@+%?=&"));secret.write_text("\n".join(lines)+"\n");secret.chmod(0o600)
print(json.dumps({"migration":migration,"test_marker":marker,"preview_counts":preview_counts,"p1_9_counts":p19_counts,"preview_role_configured":True,"credential_values_exposed":False}))
'''
        setup = (
            f"set -a; source {q(project + '/.secrets/p1_9_demo.env')}; set +a; "
            f"export PROJECT={q(project)} PYTHON_BIN={q(python)}"
        )
        result = _remote_json(client, python, setup, code, timeout=1200)
        p19.a100.checked(client, q(project + "/scripts/p1_9_demo/start_demo.sh"), timeout=180)
        print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
    finally:
        client.close()


def isolated_e2e_action() -> None:
    client, _gate = p19.a100.connect()
    try:
        root, project = p19.a100.paths(client)
        python = root + "/conda/envs/epidemic-core/bin/python"
        code = r'''import datetime as dt,hashlib,json,os,time,uuid
import sqlalchemy as sa
from sqlalchemy import text
from epidemic_core.p1_10.preview import PreviewGate,canonical_json,process_one
admin=sa.create_engine(os.environ["DEMO_ADMIN_DATABASE_URL"],hide_parameters=True,pool_pre_ping=True)
preview=sa.create_engine(os.environ["DEMO_PREVIEW_DATABASE_URL"],hide_parameters=True,pool_pre_ping=True)
trusted_tables=("data_sources","query_requests","retrieval_runs","source_fetches","raw_artifacts","normalized_observations","quality_assessments","dataset_snapshots","dataset_snapshot_observations")
def trusted_state():
 with admin.connect() as db:
  return {"counts":{name:db.execute(text('SELECT count(*) FROM epidemic.'+name)).scalar_one() for name in trusted_tables},"snapshots":[dict(r) for r in db.execute(text("SELECT id,row_count,content_sha256 FROM epidemic.dataset_snapshots ORDER BY created_at,id")).mappings()]}
before=trusted_state()
with admin.begin() as db:
 migration=db.execute(text("SELECT version_num FROM epidemic.alembic_version")).scalar_one()
 marker=db.execute(text("SELECT NOT production AND disposable AND database_name=current_database() FROM epidemic_preview.environment_markers WHERE environment='p1_10_test'")).scalar_one()
 if migration!="0018_p1_10_preview_publication" or not marker:raise RuntimeError("P1_10_TEST_GATE_INVALID")
 if db.execute(text("SELECT count(*) FROM epidemic_preview.preview_review_decisions")).scalar_one():raise RuntimeError("P1_10_TEST_NOT_EMPTY")
 p19_before={"decisions":db.execute(text("SELECT count(*) FROM epidemic_discovery.source_review_decisions")).scalar_one(),"approvals":db.execute(text("SELECT count(*) FROM epidemic_discovery.source_review_decisions WHERE decision='APPROVED'")).scalar_one(),"promotions":db.execute(text("SELECT count(*) FROM epidemic_discovery.source_promotion_runs")).scalar_one(),"publications":db.execute(text("SELECT count(*) FROM epidemic_discovery.p1_9_publications")).scalar_one()}
 if p19_before["approvals"] or p19_before["promotions"]:raise RuntimeError("P1_9_APPROVAL_REUSE_GATE_FAILED")
 disease=db.execute(text("SELECT id FROM epidemic.diseases WHERE canonical_name ILIKE '%Dengue%' ORDER BY id LIMIT 1")).scalar_one()
 request={"country_iso3":"BRA","disease_id":str(disease),"desired_metrics":["cases_new"],"time_window_kind":"latest_rolling_year","preferred_source_tiers":["NATIONAL_HEALTH","GOVERNMENT_OPEN_DATA","REGIONAL_HEALTH","INTERNATIONAL_PUBLIC_HEALTH","PUBLIC_HEALTH_RESEARCH"],"language_preferences":["pt","en"],"max_candidates":5,"retry_nonce":str(uuid.uuid4())}
 existing_run=db.execute(text("SELECT id,state FROM epidemic_discovery.discovery_runs WHERE request_json->>'country_iso3'='BRA' AND request_json ? 'retry_nonce' AND created_at>now()-interval '30 minutes' AND state IN ('QUEUED','VALIDATING_REQUEST','PLANNING','DISCOVERING','CANDIDATES_FOUND','PROFILING','FAILED_RETRYABLE') ORDER BY created_at DESC LIMIT 1")).mappings().one_or_none()
 receipt={"run_id":existing_run["id"],"created":False} if existing_run else dict(db.execute(text("SELECT * FROM epidemic_discovery.submit_keyfree_discovery_v2(CAST(:request AS jsonb))"),{"request":json.dumps(request,separators=(",",":"))}).mappings().one())
run_id=receipt["run_id"];submitted=time.monotonic();claim_delay_ms=None
with admin.connect() as db:
 first_claim=db.execute(text("SELECT e.created_at-dr.created_at FROM epidemic_discovery.discovery_run_events e JOIN epidemic_discovery.discovery_runs dr ON dr.id=e.discovery_run_id WHERE e.discovery_run_id=:id AND e.event_code='WORKER_CLAIMED' ORDER BY e.created_at,e.id LIMIT 1"),{"id":run_id}).scalar_one_or_none()
 if first_claim is not None:claim_delay_ms=max(0,int(first_claim.total_seconds()*1000))
while time.monotonic()-submitted<10:
 if claim_delay_ms is not None:break
 with admin.connect() as db:run=db.execute(text("SELECT created_at,claim_owner,heartbeat_at,state FROM epidemic_discovery.discovery_runs WHERE id=:id"),{"id":run_id}).mappings().one()
 if run["claim_owner"] is not None or run["state"]!="QUEUED":
  observed=run["heartbeat_at"] or dt.datetime.now(dt.timezone.utc);claim_delay_ms=max(0,int((observed-run["created_at"]).total_seconds()*1000));break
 time.sleep(.2)
if claim_delay_ms is None:raise RuntimeError("P1_10_DISCOVERY_CLAIM_TIMEOUT")
terminal={"REVIEW_READY","DISCOVERY_COMPLETE_NO_CANDIDATE","DISCOVERY_PARTIAL_FREE_PROVIDER_UNAVAILABLE","PROVIDER_DEGRADED","FAILED_TERMINAL","INVALID_REQUEST"};deadline=time.monotonic()+840
while True:
 with admin.connect() as db:run=db.execute(text("SELECT state,terminal_reason_code,created_at,completed_at,progress_json FROM epidemic_discovery.discovery_runs WHERE id=:id"),{"id":run_id}).mappings().one()
 if run["state"] in terminal:break
 if time.monotonic()>deadline:raise RuntimeError("P1_10_DISCOVERY_TERMINAL_TIMEOUT")
 time.sleep(1)
with admin.connect() as db:
 candidates=[dict(r) for r in db.execute(text("""SELECT c.id candidate_id,cv.id candidate_version_id,rc.id review_case_id,rc.state review_state,rc.state_version,ar.sha256 artifact_sha256,sp.schema_sha256,mp.mapping_sha256,cv.terms_sha256 license_evidence_sha256,cv.license_status,ar.byte_size,sp.row_count,sp.column_count,EXISTS(SELECT 1 FROM epidemic_discovery.discovery_run_candidate_links l WHERE l.discovery_run_id=:run AND l.candidate_version_id=cv.id) reused_existing_candidate FROM epidemic_discovery.source_candidates c JOIN epidemic_discovery.source_candidate_versions cv ON cv.candidate_id=c.id JOIN epidemic_discovery.source_review_cases rc ON rc.candidate_version_id=cv.id JOIN epidemic_discovery.quarantine_artifacts ar ON ar.candidate_version_id=cv.id JOIN epidemic_discovery.source_schema_profiles sp ON sp.candidate_version_id=cv.id JOIN epidemic_discovery.source_mapping_proposals mp ON mp.candidate_version_id=cv.id AND mp.revision=rc.current_mapping_revision WHERE c.discovery_run_id=:run OR EXISTS(SELECT 1 FROM epidemic_discovery.discovery_run_candidate_links l WHERE l.discovery_run_id=:run AND l.candidate_version_id=cv.id) ORDER BY c.created_at"""),{"run":run_id}).mappings()]
 events=[dict(r) for r in db.execute(text("SELECT event_code,details,created_at FROM epidemic_discovery.discovery_run_events WHERE discovery_run_id=:run ORDER BY created_at,id"),{"run":run_id}).mappings()]
 searches=[dict(r) for r in db.execute(text("SELECT provider_id,domain,source_tier,media_type,disposition,result_sha256,observed_at FROM epidemic_discovery.source_discovery_search_results WHERE discovery_run_id=:run ORDER BY query_id,rank"),{"run":run_id}).mappings()]
 fetches=[dict(r) for r in db.execute(text("SELECT status,http_status,content_type,byte_size,domain,elapsed_ms,artifact_sha256,failure_code FROM epidemic_discovery.candidate_fetches WHERE discovery_run_id=:run ORDER BY attempted_at,id"),{"run":run_id}).mappings()]
if run["state"]!="REVIEW_READY" or not candidates:raise RuntimeError("P1_10_REAL_CANDIDATE_REQUIRED:"+str(run["state"]))
card=candidates[0];binding={key:str(card[key]) for key in ("candidate_id","candidate_version_id","review_case_id","artifact_sha256","schema_sha256","mapping_sha256","license_evidence_sha256")};binding["discovery_run_id"]=str(run_id)
mapping={"metric_code":"notification_record_count","display_name_zh":"登革热通知记录数","unit":"notifications","data_nature":"derived_aggregation","training_eligible":False,"national_aggregate":False,"source_date_semantics":"notification_date","geography_semantics":"notification_location","quality_status":"requires_review"}
confirmations={key:True for key in ("not_trusted","license_pending","sample_partial","not_confirmed_cases","not_national","excluded_from_automation","whitelist_only","no_cross_source_sum")}
with admin.begin() as db:
 decision=db.execute(text("SELECT epidemic_preview.submit_preview_decision(CAST(:case AS uuid),:reviewer,:reason,CAST(:idem AS uuid),:version,:confirmation,CAST(:confirmations AS jsonb),CAST(:binding AS jsonb),CAST(:binding_sha AS char(64)),CAST(:mapping AS jsonb),CAST(:mapping_sha AS char(64)),:environment,:test_only)"),{"case":card["review_case_id"],"reviewer":"p1-10-isolated-e2e","reason":"隔离测试：确认仅发布候选预览并保留许可与样本限制。","idem":str(uuid.uuid4()),"version":card["state_version"],"confirmation":"发布候选预览","confirmations":canonical_json(confirmations),"binding":canonical_json(binding),"binding_sha":hashlib.sha256(canonical_json(binding).encode()).hexdigest(),"mapping":canonical_json(mapping),"mapping_sha":card["mapping_sha256"],"environment":"p1_10_test","test_only":True}).scalar_one()
 job=db.execute(text("SELECT epidemic_preview.queue_preview_publication(CAST(:decision AS uuid),CAST(:idem AS uuid))"),{"decision":decision,"idem":str(uuid.uuid4())}).scalar_one()
queued=time.monotonic();os.environ["APP_ENV"]="p1_10_test";os.environ["DISCOVERY_QUARANTINE_ROOT"]=str(os.path.join(os.environ["PROJECT"],"storage/p1_9_demo/quarantine/sha256"));gate=PreviewGate.verify(preview)
if not process_one(preview,gate,"p1-10-isolated-e2e-worker"):raise RuntimeError("P1_10_PREVIEW_JOB_NOT_CLAIMED")
preview_claim_ms=int((time.monotonic()-queued)*1000)
with admin.connect() as db:
 job_row=dict(db.execute(text("SELECT id,state,attempt_count,failure_code,snapshot_id,publication_id FROM epidemic_preview.preview_publication_jobs WHERE id=:id"),{"id":job}).mappings().one())
 publication=dict(db.execute(text("SELECT id,snapshot_id,observation_count,total_value,date_min,date_max,state_count,municipality_count,snapshot_sha256,publication_sha256,production_usable,training_eligible,national_aggregate,coverage_status,data_tier FROM epidemic_preview.active_preview_publications_v1 WHERE publication_job_id=:job"),{"job":job}).mappings().one())
 obs=db.execute(text("SELECT count(*) FROM epidemic_preview.preview_observations WHERE publication_job_id=:job"),{"job":job}).scalar_one()
 quality=dict(db.execute(text("SELECT status,pii_status,coverage_status,observation_count,total_value,assessment_sha256 FROM epidemic_preview.preview_quality_assessments WHERE publication_job_id=:job"),{"job":job}).mappings().one())
 lineage=dict(db.execute(text("SELECT artifact_sha256,schema_sha256,source_mapping_sha256,license_evidence_sha256,binding_sha256,contract_sha256,snapshot_sha256,assessment_sha256 FROM epidemic_preview.preview_lineage_v1 WHERE publication_job_id=:job"),{"job":job}).mappings().one())
 p19={"decisions":db.execute(text("SELECT count(*) FROM epidemic_discovery.source_review_decisions")).scalar_one(),"approvals":db.execute(text("SELECT count(*) FROM epidemic_discovery.source_review_decisions WHERE decision='APPROVED'")).scalar_one(),"promotions":db.execute(text("SELECT count(*) FROM epidemic_discovery.source_promotion_runs")).scalar_one(),"publications":db.execute(text("SELECT count(*) FROM epidemic_discovery.p1_9_publications")).scalar_one()}
after=trusted_state()
if before!=after:raise RuntimeError("P1_10_TRUSTED_DATA_DRIFT")
if job_row["state"]!="PREVIEW_PUBLISHED" or obs<1 or p19!=p19_before:raise RuntimeError("P1_10_PREVIEW_E2E_FAILED")
safe_events=[]
for event in events:
 details=event.get("details") or {};safe_events.append({"event_code":event["event_code"],"provider_id":details.get("provider_id"),"domain":details.get("domain"),"network_request":details.get("network_request"),"http_status":details.get("http_status"),"elapsed_ms":details.get("elapsed_ms"),"result_count":details.get("result_count"),"reason_code":details.get("reason_code")})
result={"migration":migration,"discovery":{"run_id":str(run_id),"created":receipt["created"],"claim_delay_ms":claim_delay_ms,"state":run["state"],"reason":run["terminal_reason_code"],"events":safe_events,"searches":searches,"fetches":fetches,"candidate_count":len(candidates),"candidate":card},"preview":{"decision_id":str(decision),"job":job_row,"claim_and_process_ms":preview_claim_ms,"publication":publication,"observations":obs,"quality":quality,"lineage":lineage},"p1_9_boundaries":p19,"trusted_data_unchanged":True}
print(json.dumps(result,default=str))
'''
        setup = (
            f"set -a; source {q(project + '/.secrets/p1_9_demo.env')}; set +a; "
            f"export PROJECT={q(project)} PYTHONPATH={q(project + '/src')}"
        )
        result = _remote_json(client, python, setup, code, timeout=1200)
        print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
    finally:
        client.close()


def isolated_diagnostics_action() -> None:
    client, _gate = p19.a100.connect()
    try:
        root, project = p19.a100.paths(client)
        python = root + "/conda/envs/epidemic-core/bin/python"
        code = r'''import json,os
import sqlalchemy as sa
from sqlalchemy import text
engine=sa.create_engine(os.environ["DEMO_ADMIN_DATABASE_URL"],hide_parameters=True)
with engine.connect() as db:
 run=dict(db.execute(text("SELECT id,state,terminal_reason_code,progress_json,created_at,completed_at FROM epidemic_discovery.discovery_runs ORDER BY created_at DESC LIMIT 1")).mappings().one())
 links=[dict(r) for r in db.execute(text("SELECT discovery_run_id,candidate_id,candidate_version_id,artifact_sha256,link_reason,linked_at FROM epidemic_discovery.discovery_run_candidate_links ORDER BY linked_at DESC LIMIT 3")).mappings()]
 job=db.execute(text("SELECT id,state,attempt_count,failure_code,failure_detail_sha256,snapshot_id,publication_id,created_at,updated_at FROM epidemic_preview.preview_publication_jobs ORDER BY created_at DESC LIMIT 1")).mappings().one_or_none()
 events=[] if job is None else [dict(r) for r in db.execute(text("SELECT from_state,to_state,event_code,attempt,error_code,requires_user_action,next_step_zh,details,created_at FROM epidemic_preview.preview_publication_events WHERE publication_job_id=:id ORDER BY id"),{"id":job["id"]}).mappings()]
 counts={name:db.execute(text('SELECT count(*) FROM epidemic_preview.'+name)).scalar_one() for name in ('preview_review_decisions','preview_publication_jobs','preview_staging_observations','preview_mapping_contracts','preview_observations','preview_quality_assessments','preview_snapshots','preview_dashboard_publications')}
print(json.dumps({"latest_discovery":run,"links":links,"latest_job":dict(job) if job else None,"events":events,"counts":counts},default=str))
'''
        setup = f"set -a; source {q(project + '/.secrets/p1_9_demo.env')}; set +a"
        result = _remote_json(client, python, setup, code, timeout=120)
        print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
    finally:
        client.close()


def formal_deploy_action() -> None:
    client, _gate = p19.a100.connect()
    try:
        root, project = p19.a100.paths(client)
        python = root + "/conda/envs/epidemic-core/bin/python"
        code = r'''import hashlib,json,os,pathlib,secrets,shlex,subprocess
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import make_url
project=pathlib.Path(os.environ["PROJECT"]);python=os.environ["PYTHON_BIN"];pgbin=pathlib.Path(os.environ["POSTGRES_BIN"])
formal=make_url(os.environ["MIGRATION_DATABASE_URL"]).set(username="epidemicdb",password=os.environ["POSTGRES_ADMIN_PASSWORD"])
engine=sa.create_engine(formal,hide_parameters=True,pool_pre_ping=True)
tables=("data_sources","query_requests","retrieval_runs","source_fetches","raw_artifacts","normalized_observations","quality_assessments","dataset_snapshots","dataset_snapshot_observations")
def inventory():
 with engine.connect() as db:
  candidate=dict(db.execute(text("""SELECT c.id candidate_id,c.status,rc.id review_case_id,rc.state,(SELECT count(*) FROM epidemic_discovery.source_review_decisions d WHERE d.review_case_id=rc.id AND d.decision='APPROVED') approvals,(SELECT count(*) FROM epidemic_discovery.source_review_decisions d WHERE d.review_case_id=rc.id AND d.decision='REJECTED') rejections,(SELECT count(*) FROM epidemic_discovery.source_promotion_runs p JOIN epidemic_discovery.source_review_decisions d ON d.id=p.review_decision_id WHERE d.review_case_id=rc.id) promotions FROM epidemic_discovery.source_candidates c JOIN epidemic_discovery.source_candidate_versions cv ON cv.candidate_id=c.id JOIN epidemic_discovery.source_review_cases rc ON rc.candidate_version_id=cv.id WHERE c.publisher ILIKE '%Brazil%' OR c.publisher ILIKE '%DATASUS%' ORDER BY cv.created_at DESC LIMIT 1""")).mappings().one())
  return {"migration":db.execute(text("SELECT version_num FROM epidemic.alembic_version")).scalar_one(),"counts":{name:db.execute(text('SELECT count(*) FROM epidemic.'+name)).scalar_one() for name in tables},"snapshots":[dict(r) for r in db.execute(text("SELECT id,row_count,content_sha256 FROM epidemic.dataset_snapshots ORDER BY created_at,id")).mappings()],"daily":[dict(r) for r in db.execute(text("SELECT schedule_id,data_source_id,query_request_id,enabled,state_version FROM epidemic.daily_update_schedules ORDER BY schedule_id")).mappings()],"training":db.execute(text("SELECT count(*) FROM epidemic.training_snapshot_observations_v1")).scalar_one(),"candidate":candidate}
before=inventory()
if before["migration"]!="0017_p1_9_crypto_resolution" or before["candidate"]["state"]!="REVIEW_READY" or before["candidate"]["approvals"] or before["candidate"]["rejections"] or before["candidate"]["promotions"]:raise RuntimeError("P1_10_FORMAL_PREDEPLOY_GATE_FAILED")
backup_dir=project/"storage/backups/p1_10"/("formal_"+secrets.token_hex(6));backup_dir.mkdir(parents=True,mode=0o700);dump=backup_dir/"pre_formal_migration.dump"
env=os.environ.copy();env["PGPASSWORD"]=formal.password or ""
args=[str(pgbin/"pg_dump"),"-h",formal.host or "127.0.0.1","-p",str(formal.port or 5432),"-U",formal.username or "epidemicdb","-d",formal.database or "epidemic_openclaw","--format=custom","--no-owner","--no-privileges","--file",str(dump)]
result=subprocess.run(args,cwd=project,env=env,text=True,capture_output=True,timeout=1800)
if result.returncode:raise RuntimeError("P1_10_FORMAL_BACKUP_FAILED")
dump.chmod(0o600);backup_sha=hashlib.sha256(dump.read_bytes()).hexdigest()
password=secrets.token_urlsafe(36)
with engine.begin() as db:
 db.execute(text("SELECT set_config('p1_10.preview_password',:password,true)"),{"password":password})
 db.exec_driver_sql("DO $$BEGIN IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='epidemicpreview') THEN EXECUTE format('CREATE ROLE epidemicpreview LOGIN PASSWORD %%L NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT',current_setting('p1_10.preview_password')); ELSE EXECUTE format('ALTER ROLE epidemicpreview LOGIN PASSWORD %%L NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT',current_setting('p1_10.preview_password')); END IF; END$$")
url=sa.URL.create("postgresql+psycopg",username="epidemicpreview",password=password,host=formal.host or "127.0.0.1",port=formal.port or 5432,database=formal.database or "epidemic_openclaw").render_as_string(hide_password=False)
config=project/".secrets/preview.env";config.write_text("PREVIEW_DATABASE_URL="+shlex.quote(url)+"\n");config.chmod(0o600)
upgrade_env=os.environ.copy();upgrade_env["MIGRATION_DATABASE_URL"]=formal.render_as_string(hide_password=False)
upgrade=subprocess.run([python,"-m","alembic","upgrade","head"],cwd=project,env=upgrade_env,text=True,capture_output=True,timeout=900)
if upgrade.returncode:raise RuntimeError("P1_10_FORMAL_MIGRATION_FAILED:"+hashlib.sha256(upgrade.stderr.encode()).hexdigest())
after=inventory()
with engine.connect() as db:
 preview={"decisions":db.execute(text("SELECT count(*) FROM epidemic_preview.preview_review_decisions")).scalar_one(),"jobs":db.execute(text("SELECT count(*) FROM epidemic_preview.preview_publication_jobs")).scalar_one(),"publications":db.execute(text("SELECT count(*) FROM epidemic_preview.preview_dashboard_publications")).scalar_one(),"links":db.execute(text("SELECT count(*) FROM epidemic_discovery.discovery_run_candidate_links")).scalar_one(),"marker":db.execute(text("SELECT production AND NOT disposable AND database_name=current_database() FROM epidemic_preview.environment_markers WHERE environment='production'")).scalar_one()}
 permissions={"role_login":db.execute(text("SELECT rolcanlogin AND NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole FROM pg_roles WHERE rolname='epidemicpreview'")).scalar_one(),"trusted_write":db.execute(text("SELECT has_table_privilege('epidemicpreview','epidemic.normalized_observations','INSERT,UPDATE,DELETE')")).scalar_one(),"preview_write":db.execute(text("SELECT has_table_privilege('epidemicpreview','epidemic_preview.preview_observations','INSERT,UPDATE')")).scalar_one(),"staging_delete":db.execute(text("SELECT has_table_privilege('epidemicpreview','epidemic_preview.preview_staging_observations','DELETE')")).scalar_one()}
if before["counts"]!=after["counts"] or before["snapshots"]!=after["snapshots"] or before["daily"]!=after["daily"] or before["training"]!=after["training"]:raise RuntimeError("P1_10_FORMAL_TRUSTED_DRIFT")
if after["migration"]!="0018_p1_10_preview_publication" or any(preview[k] for k in ("decisions","jobs","publications","links")) or not preview["marker"]:raise RuntimeError("P1_10_FORMAL_PREVIEW_EMPTY_GATE_FAILED")
if not permissions["role_login"] or permissions["trusted_write"] or not permissions["preview_write"] or not permissions["staging_delete"]:raise RuntimeError("P1_10_FORMAL_ROLE_GATE_FAILED")
print(json.dumps({"before":before,"after":after,"preview":preview,"permissions":permissions,"backup_path":str(dump).replace(str(project),"[project]"),"backup_sha256":backup_sha,"backup_bytes":dump.stat().st_size,"credential_values_exposed":False},default=str))
'''
        setup = (
            f"source {q(project + '/scripts/database/_env.sh')}; set -a; "
            f"source {q(project + '/.secrets/postgres.env')}; set +a; "
            f"export PROJECT={q(project)} PYTHON_BIN={q(python)} POSTGRES_BIN={q(root + '/conda/envs/postgresql18/bin')}"
        )
        database = _remote_json(client, python, setup, code, timeout=3600)
        stop_scripts = (
            "scripts/source_review/stop_source_review.sh",
            "scripts/dashboard/stop_dashboard.sh",
            "scripts/source_discovery_worker/stop_source_discovery_worker.sh",
            "scripts/preview_publication_worker/stop_preview_publication_worker.sh",
        )
        for relative in stop_scripts:
            p19.a100.run(client, q(project + "/" + relative), timeout=180)
        p19.a100.checked(client, q(project + "/scripts/source_review/install_runtime.sh"), timeout=180)
        p19.a100.checked(client, q(project + "/scripts/dashboard/install_runtime.sh"), timeout=180)
        start_scripts = (
            "scripts/source_discovery_worker/start_source_discovery_worker.sh",
            "scripts/source_review/start_source_review.sh",
            "scripts/dashboard/start_dashboard.sh",
            "scripts/preview_publication_worker/start_preview_publication_worker.sh",
        )
        service_results: dict[str, bool] = {}
        for relative in start_scripts:
            rc, _out, _err = p19.a100.run(client, q(project + "/" + relative), timeout=240)
            service_results[relative] = rc == 0
        checks = (
            "scripts/source_discovery_worker/status_source_discovery_worker.sh",
            "scripts/source_review/status_source_review.sh",
            "scripts/dashboard/status_dashboard.sh",
            "scripts/preview_publication_worker/status_preview_publication_worker.sh",
        )
        for relative in checks:
            rc, _out, _err = p19.a100.run(client, q(project + "/" + relative), timeout=120)
            service_results[relative] = rc == 0
        if not all(service_results.values()):
            raise RuntimeError("P1_10_FORMAL_SERVICE_DEPLOY_FAILED")
        print(json.dumps({"database": database, "services": service_results}, ensure_ascii=True, indent=2, sort_keys=True))
    finally:
        client.close()


def _isolated_pytest_result(client: Any, root: str, project: str) -> dict[str, object]:
    python = root + "/conda/envs/epidemic-core/bin/python"
    code = r'''import hashlib,json,os,pathlib,re,secrets,subprocess,time
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import make_url
project=pathlib.Path(os.environ["PROJECT"]);python=os.environ["PYTHON_BIN"]
formal=make_url(os.environ["MIGRATION_DATABASE_URL"]).set(username="epidemicdb",password=os.environ["POSTGRES_ADMIN_PASSWORD"]);app=make_url(os.environ["APP_DATABASE_URL"]);admin=formal.set(database="postgres")
name="epidemic_p1_10_pytest_"+secrets.token_hex(5);env=os.environ.copy()
def run(args,timeout=1800,run_env=None):return subprocess.run(args,cwd=project,env=run_env or env,text=True,capture_output=True,timeout=timeout)
started=time.monotonic();admin_engine=sa.create_engine(admin,isolation_level="AUTOCOMMIT",hide_parameters=True)
with admin_engine.connect() as db:db.exec_driver_sql('CREATE DATABASE "'+name+'" TEMPLATE template0 ENCODING \'UTF8\'')
try:
 test_env=os.environ.copy();test_env["MIGRATION_DATABASE_URL"]=formal.set(database=name).render_as_string(hide_password=False);test_env["APP_DATABASE_URL"]=app.set(database=name).render_as_string(hide_password=False);test_env["PYTHONPATH"]="src"
 test_engine=sa.create_engine(formal.set(database=name),hide_parameters=True)
 with test_engine.begin() as db:
  db.exec_driver_sql("CREATE SCHEMA IF NOT EXISTS epidemic")
  db.exec_driver_sql("GRANT USAGE ON SCHEMA epidemic TO epidemic_app")
 test_engine.dispose()
 migrate=run([python,"-m","alembic","upgrade","head"],900,test_env)
 if migrate.returncode:
  diagnostic=re.sub(r"postgresql(?:\+\w+)?://[^\s]+","[DATABASE_URL_REDACTED]",migrate.stderr).replace(str(project),"[project]").splitlines()[-12:]
  raise RuntimeError("PYTEST_MIGRATION_FAILED:"+hashlib.sha256(migrate.stderr.encode()).hexdigest()+":"+" | ".join(diagnostic))
 seed=run([python,"-m","epidemic_core.cli","seed-reference"],300,test_env)
 if seed.returncode:raise RuntimeError("PYTEST_SEED_FAILED:"+hashlib.sha256(seed.stderr.encode()).hexdigest())
 result=run([python,"-m","pytest","-p","no:cacheprovider","-q"],3600,test_env)
 safe=(result.stdout+"\n"+result.stderr).replace(str(project),"[project]").replace(os.environ["DATA_ROOT"],"[data-root]")
 safe_lines=safe.splitlines();payload={"label":"pytest_full_isolated","returncode":result.returncode,"duration_seconds":round(time.monotonic()-started,3),"output_sha256":hashlib.sha256(safe.encode()).hexdigest(),"tail":safe_lines[-40:],"database_disposable":True,"database_initialized_from_migrations":True,"reference_seeded":True,"formal_database_written":False}
 print(json.dumps(payload))
finally:
 with admin_engine.connect() as db:
  db.execute(text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=:name"),{"name":name});db.exec_driver_sql('DROP DATABASE IF EXISTS "'+name+'"')
 admin_engine.dispose()
'''
    setup = (
        f"source {q(project + '/scripts/database/_env.sh')}; set -a; source {q(project + '/.secrets/postgres.env')}; set +a; "
        f"export PROJECT={q(project)} PYTHON_BIN={q(python)} POSTGRES_BIN={q(root + '/conda/envs/postgresql18/bin')} DATA_ROOT={q(root)}"
    )
    return _remote_json(client, python, setup, code, timeout=5400)


def isolated_pytest_action() -> None:
    client, _gate = p19.a100.connect()
    try:
        root, project = p19.a100.paths(client)
        result = _isolated_pytest_result(client, root, project)
        print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
        if result["returncode"] != 0:
            raise SystemExit(1)
    finally:
        client.close()


def contract_tests_action() -> None:
    client, _gate = p19.a100.connect()
    try:
        root, project = p19.a100.paths(client)
        python = root + "/conda/envs/epidemic-core/bin/python"
        code = r'''import concurrent.futures,datetime as dt,hashlib,json,os,pathlib,secrets,subprocess,uuid
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import make_url
from epidemic_core.p1_10.preview import PreviewGate,_transition,claim_one,process_claim,sha
project=pathlib.Path(os.environ["PROJECT"]);pgbin=pathlib.Path(os.environ["POSTGRES_BIN"]);source_admin=make_url(os.environ["DEMO_ADMIN_DATABASE_URL"]);source_preview=make_url(os.environ["DEMO_PREVIEW_DATABASE_URL"]);cluster_admin=source_admin.set(database="postgres")
name="epidemic_p1_10_contract_"+secrets.token_hex(5);dump=project/"storage/backups/p1_10"/("contract_"+secrets.token_hex(5)+".dump");dump.parent.mkdir(parents=True,exist_ok=True);os.chmod(dump.parent,0o700);env=os.environ.copy();env["PGPASSWORD"]=source_admin.password or ""
def pg_args(database):return ["-h",source_admin.host or "127.0.0.1","-p",str(source_admin.port or 15432),"-U",source_admin.username or "postgres","-d",database]
def run(args,timeout=1800):return subprocess.run(args,cwd=project,env=env,text=True,capture_output=True,timeout=timeout)
dump_result=run([str(pgbin/"pg_dump"),*pg_args(source_admin.database or "epidemic_p1_9_demo"),"--format=custom","--no-owner","--file",str(dump)])
if dump_result.returncode:raise RuntimeError("CONTRACT_DUMP_FAILED:"+hashlib.sha256(dump_result.stderr.encode()).hexdigest())
os.chmod(dump,0o600);cluster=sa.create_engine(cluster_admin,isolation_level="AUTOCOMMIT",hide_parameters=True)
with cluster.connect() as db:db.exec_driver_sql('CREATE DATABASE "'+name+'" TEMPLATE template0 ENCODING \'UTF8\'')
try:
 restore=run([str(pgbin/"pg_restore"),*pg_args(name),"--exit-on-error","--no-owner",str(dump)])
 if restore.returncode:raise RuntimeError("CONTRACT_RESTORE_FAILED:"+hashlib.sha256(restore.stderr.encode()).hexdigest())
 admin_url=source_admin.set(database=name);preview_url=source_preview.set(database=name);admin=sa.create_engine(admin_url,hide_parameters=True,pool_size=20,max_overflow=10);preview=sa.create_engine(preview_url,hide_parameters=True,pool_size=20,max_overflow=10)
 with cluster.connect() as db:
  for role in (source_preview.username,"p1_9_demo_review","p1_9_demo_dashboard","p1_9_demo_app"):
   if role:db.exec_driver_sql('GRANT CONNECT ON DATABASE "'+name+'" TO "'+role+'"')
 with admin.begin() as db:
  db.execute(text("UPDATE epidemic_preview.environment_markers SET database_name=current_database() WHERE environment='p1_10_test'"))
  before={"trusted_observations":db.execute(text("SELECT count(*) FROM epidemic.normalized_observations")).scalar_one(),"trusted_snapshots":[dict(r) for r in db.execute(text("SELECT id,row_count,content_sha256 FROM epidemic.dataset_snapshots ORDER BY created_at,id")).mappings()],"training":db.execute(text("SELECT count(*) FROM epidemic.training_snapshot_observations_v1")).scalar_one(),"daily":[r[0] for r in db.execute(text("SELECT to_jsonb(s) FROM epidemic.daily_update_schedules s ORDER BY to_jsonb(s)::text"))]}
  decision=dict(db.execute(text("SELECT * FROM epidemic_preview.preview_review_decisions ORDER BY created_at LIMIT 1")).mappings().one());job=dict(db.execute(text("SELECT * FROM epidemic_preview.preview_publication_jobs WHERE preview_decision_id=:id"),{"id":decision["id"]}).mappings().one())
 confirmations=dict(decision["confirmations"]);binding=dict(decision["binding_json"]);mapping=dict(decision["mapping_json"])
 decision_sql=text("SELECT epidemic_preview.submit_preview_decision(:case,:reviewer,:reason,:idem,:version,:confirmation,CAST(:confirmations AS jsonb),CAST(:binding AS jsonb),:binding_sha,CAST(:mapping AS jsonb),:mapping_sha,'p1_10_test',true)")
 decision_params={"case":decision["review_case_id"],"reviewer":"contract-reviewer","reason":"isolated contract verification only","version":decision["source_state_version"],"confirmation":"\u53d1\u5e03\u5019\u9009\u9884\u89c8","confirmations":json.dumps(confirmations),"binding":json.dumps(binding),"binding_sha":decision["binding_sha256"],"mapping":json.dumps(mapping),"mapping_sha":decision["mapping_sha256"]}
 def submit_one(_):
  with admin.begin() as db:return str(db.execute(decision_sql,{**decision_params,"idem":uuid.uuid4()}).scalar_one())
 with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:decision_ids=list(executor.map(submit_one,range(10)))
 def queue_one(_):
  with admin.begin() as db:return str(db.execute(text("SELECT epidemic_preview.queue_preview_publication(:decision,:idem)"),{"decision":decision["id"],"idem":uuid.uuid4()}).scalar_one())
 with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:job_ids=list(executor.map(queue_one,range(10)))
 def reject(label,expected,params=None,sql=decision_sql):
  try:
   with admin.begin() as db:db.execute(sql,params or {})
  except Exception as exc:
   value=expected in str(exc)
   return {"scenario":label,"expected_code":expected,"pass":value}
  return {"scenario":label,"expected_code":expected,"pass":False}
 negative=[]
 missing=dict(confirmations);missing.pop("not_trusted")
 negative.append(reject("missing_confirmation","PREVIEW_CONFIRMATIONS_INCOMPLETE",{**decision_params,"idem":uuid.uuid4(),"confirmations":json.dumps(missing)}))
 negative.append(reject("wrong_confirmation_text","PREVIEW_CONFIRMATION_TEXT_INVALID",{**decision_params,"idem":uuid.uuid4(),"confirmation":"wrong"}))
 demo_sql=text(str(decision_sql).replace("'p1_10_test',true","'p1_9_demo',true"))
 negative.append(reject("p1_9_demo_decision","P1_9_DEMO_APPROVAL_FORBIDDEN",{**decision_params,"idem":uuid.uuid4()},demo_sql))
 bad_binding=dict(binding);bad_binding["artifact_sha256"]="0"*64
 negative.append(reject("artifact_binding_drift","PREVIEW_BINDING_MISMATCH",{**decision_params,"idem":uuid.uuid4(),"binding":json.dumps(bad_binding)}))
 bad_mapping_binding=dict(binding);bad_mapping_binding["mapping_sha256"]="f"*64
 negative.append(reject("mapping_binding_drift","PREVIEW_BINDING_MISMATCH",{**decision_params,"idem":uuid.uuid4(),"binding":json.dumps(bad_mapping_binding)}))
 negative.append(reject("job_without_decision","VALID_PREVIEW_DECISION_REQUIRED",{"decision":uuid.uuid4(),"idem":uuid.uuid4()},text("SELECT epidemic_preview.queue_preview_publication(:decision,:idem)")))
 with admin.connect() as db:
  concurrency={"decision_attempts":10,"unique_decision_ids":len(set(decision_ids)),"decision_row_count":db.execute(text("SELECT count(*) FROM epidemic_preview.preview_review_decisions")).scalar_one(),"job_attempts":10,"unique_job_ids":len(set(job_ids)),"job_row_count":db.execute(text("SELECT count(*) FROM epidemic_preview.preview_publication_jobs")).scalar_one()}
 def clear_publication():
  with admin.begin() as db:
   db.execute(text("SET LOCAL session_replication_role='replica'"))
   db.execute(text("UPDATE epidemic_preview.preview_publication_jobs SET state='PREVIEW_QUEUED',claim_owner=NULL,claim_token=NULL,lease_expires_at=NULL,heartbeat_at=NULL,next_attempt_at=NULL,attempt_count=0,failure_code=NULL,failure_detail_sha256=NULL,snapshot_id=NULL,publication_id=NULL,observation_count=0 WHERE id=:job"),{"job":job["id"]})
   db.execute(text("DELETE FROM epidemic_preview.preview_dashboard_publications WHERE publication_job_id=:job"),{"job":job["id"]})
   db.execute(text("DELETE FROM epidemic_preview.preview_snapshot_observations WHERE snapshot_id IN (SELECT id FROM epidemic_preview.preview_snapshots WHERE publication_job_id=:job)"),{"job":job["id"]})
   for table in ("preview_snapshots","preview_quality_assessments","preview_observations","preview_staging_observations","preview_mapping_contracts"):
    db.execute(text("DELETE FROM epidemic_preview."+table+" WHERE publication_job_id=:job"),{"job":job["id"]})
 clear_publication();gate=PreviewGate.verify(preview)
 future_token=uuid.uuid4();old_token=future_token
 with admin.begin() as db:db.execute(text("UPDATE epidemic_preview.preview_publication_jobs SET state='PREVIEW_CLAIMED',claim_owner='future-owner',claim_token=:token,lease_expires_at=now()+interval '10 minutes' WHERE id=:job"),{"token":future_token,"job":job["id"]})
 future_claim_blocked=claim_one(preview,gate,"competing-owner") is None
 with admin.begin() as db:db.execute(text("UPDATE epidemic_preview.preview_publication_jobs SET lease_expires_at=now()-interval '1 second' WHERE id=:job"),{"job":job["id"]})
 recovered=claim_one(preview,gate,"recovery-owner");expired_recovered=recovered is not None
 try:_transition(preview,job["id"],old_token,"PREVIEW_VALIDATING_DECISION","OLD_TOKEN_TEST");old_token_rejected=False
 except RuntimeError as exc:old_token_rejected=str(exc)=="STALE_PREVIEW_CLAIM"
 fault_results=[]
 for phase in ("staging","quality","snapshot","publication"):
  clear_publication();claim=claim_one(preview,gate,"fault-"+phase)
  if claim is None:raise RuntimeError("FAULT_CLAIM_MISSING_"+phase)
  os.environ["P1_10_FAULT_PHASE"]=phase;process_claim(preview,gate,*claim);os.environ.pop("P1_10_FAULT_PHASE",None)
  with admin.connect() as db:
   counts={table:db.execute(text("SELECT count(*) FROM epidemic_preview."+table+" WHERE publication_job_id=:job"),{"job":job["id"]}).scalar_one() for table in ("preview_mapping_contracts","preview_observations","preview_quality_assessments","preview_snapshots","preview_dashboard_publications")}
   state=db.execute(text("SELECT state FROM epidemic_preview.preview_publication_jobs WHERE id=:job"),{"job":job["id"]}).scalar_one()
  fault_results.append({"phase":phase,"state":state,"partial_publication_counts":counts,"pass":state=="FAILED_RETRYABLE" and not any(counts.values())})
 clear_publication();claim=claim_one(preview,gate,"success-owner")
 if claim is None:raise RuntimeError("SUCCESS_CLAIM_MISSING")
 process_claim(preview,gate,*claim)
 with admin.connect() as db:
  success_status=dict(db.execute(text("SELECT state,failure_code FROM epidemic_preview.preview_publication_jobs WHERE id=:job"),{"job":job["id"]}).mappings().one())
  if success_status["state"]!="PREVIEW_PUBLISHED":raise RuntimeError("SUCCESS_PUBLICATION_FAILED:"+str(success_status))
  published_counts={table:db.execute(text("SELECT count(*) FROM epidemic_preview."+table+" WHERE publication_job_id=:job"),{"job":job["id"]}).scalar_one() for table in ("preview_mapping_contracts","preview_observations","preview_quality_assessments","preview_snapshots","preview_dashboard_publications")}
  snapshot=dict(db.execute(text("SELECT * FROM epidemic_preview.preview_snapshots WHERE publication_job_id=:job"),{"job":job["id"]}).mappings().one())
  rows=[dict(r) for r in db.execute(text("SELECT id,observation_date,state_code,municipality_code,value_numeric,source_row_set_sha256 FROM epidemic_preview.preview_observations WHERE publication_job_id=:job ORDER BY observation_date,state_code,municipality_code"),{"job":job["id"]}).mappings()]
 recompute_payload=[{"id":str(r["id"]),"date":r["observation_date"].isoformat(),"state":r["state_code"],"municipality":r["municipality_code"],"value":int(r["value_numeric"]),"source_rows":r["source_row_set_sha256"]} for r in rows];snapshot_recomputed=sha(recompute_payload)==snapshot["snapshot_sha256"]
 before_repeat={"counts":published_counts,"snapshot_id":str(snapshot["id"]),"snapshot_sha256":snapshot["snapshot_sha256"]}
 with admin.begin() as db:db.execute(text("UPDATE epidemic_preview.preview_publication_jobs SET state='PREVIEW_QUEUED',claim_owner=NULL,claim_token=NULL,lease_expires_at=NULL,next_attempt_at=NULL WHERE id=:job"),{"job":job["id"]})
 repeat_claim=claim_one(preview,gate,"repeat-owner")
 if repeat_claim is None:raise RuntimeError("REPEAT_CLAIM_MISSING")
 process_claim(preview,gate,*repeat_claim)
 with admin.connect() as db:
  repeat_counts={table:db.execute(text("SELECT count(*) FROM epidemic_preview."+table+" WHERE publication_job_id=:job"),{"job":job["id"]}).scalar_one() for table in ("preview_mapping_contracts","preview_observations","preview_quality_assessments","preview_snapshots","preview_dashboard_publications")}
  repeat_snapshot=dict(db.execute(text("SELECT id,snapshot_sha256 FROM epidemic_preview.preview_snapshots WHERE publication_job_id=:job"),{"job":job["id"]}).mappings().one());publication_id=db.execute(text("SELECT id FROM epidemic_preview.preview_dashboard_publications WHERE publication_job_id=:job"),{"job":job["id"]}).scalar_one()
 with admin.begin() as db:revoked=db.execute(text("SELECT epidemic_preview.revoke_preview_publication(:publication,'contract-reviewer','isolated revoke lifecycle verification',:idem)"),{"publication":publication_id,"idem":uuid.uuid4()}).scalar_one()
 with admin.connect() as db:
  after={"trusted_observations":db.execute(text("SELECT count(*) FROM epidemic.normalized_observations")).scalar_one(),"trusted_snapshots":[dict(r) for r in db.execute(text("SELECT id,row_count,content_sha256 FROM epidemic.dataset_snapshots ORDER BY created_at,id")).mappings()],"training":db.execute(text("SELECT count(*) FROM epidemic.training_snapshot_observations_v1")).scalar_one(),"daily":[r[0] for r in db.execute(text("SELECT to_jsonb(s) FROM epidemic.daily_update_schedules s ORDER BY to_jsonb(s)::text"))]}
  revoke_result={"revoked":bool(revoked),"active_publications":db.execute(text("SELECT count(*) FROM epidemic_preview.active_preview_publications_v1")).scalar_one(),"audit_publications":db.execute(text("SELECT count(*) FROM epidemic_preview.preview_dashboard_publications")).scalar_one(),"snapshots_retained":db.execute(text("SELECT count(*) FROM epidemic_preview.preview_snapshots")).scalar_one(),"observations_retained":db.execute(text("SELECT count(*) FROM epidemic_preview.preview_observations")).scalar_one()}
  event_states=[r[0] for r in db.execute(text("SELECT DISTINCT to_state FROM epidemic_preview.preview_publication_events WHERE publication_job_id=:job ORDER BY to_state"),{"job":job["id"]})]
 def denied(role,sql):
  try:
   with admin.connect() as db:
    tx=db.begin();db.execute(text('SET LOCAL ROLE "'+role+'"'));db.execute(text(sql));tx.rollback()
   return False
  except Exception:return True
 permissions={"preview_worker_trusted_write_denied":denied(source_preview.username or "p1_10_demo_preview","INSERT INTO epidemic.normalized_observations(id) VALUES(gen_random_uuid())"),"review_observation_write_denied":denied("p1_9_demo_review","INSERT INTO epidemic_preview.preview_observations(id) VALUES(gen_random_uuid())"),"daily_preview_read_denied":denied("p1_9_demo_app","SELECT count(*) FROM epidemic_preview.preview_observations")}
 result={"concurrency":concurrency,"negative_gates":negative,"lease":{"unexpired_not_claimed":future_claim_blocked,"expired_recovered":expired_recovered,"old_token_rejected":old_token_rejected},"fault_injection":fault_results,"publication":{"nonempty":all(value>0 for value in published_counts.values()),"counts":published_counts,"snapshot_recomputed":snapshot_recomputed,"repeat_reused":repeat_counts==before_repeat["counts"] and str(repeat_snapshot["id"])==before_repeat["snapshot_id"] and repeat_snapshot["snapshot_sha256"]==before_repeat["snapshot_sha256"]},"revoke":revoke_result,"state_machine":{"states":event_states,"append_only_audit_retained":revoke_result["audit_publications"]==1},"permissions":permissions,"isolation":{"trusted_unchanged":before==after,"daily_unchanged":before["daily"]==after["daily"],"training_unchanged":before["training"]==after["training"]},"fixture":{"disposable_clone":True,"trigger_bypass_transaction_local_only":True,"production_triggers_modified":False},"temporary_database":name}
 result["pass"]=concurrency["unique_decision_ids"]==1 and concurrency["decision_row_count"]==1 and concurrency["unique_job_ids"]==1 and concurrency["job_row_count"]==1 and all(x["pass"] for x in negative) and all(result["lease"].values()) and all(x["pass"] for x in fault_results) and all(result["publication"][k] for k in ("nonempty","snapshot_recomputed","repeat_reused")) and revoke_result["active_publications"]==0 and revoke_result["audit_publications"]==1 and revoke_result["snapshots_retained"]==1 and revoke_result["observations_retained"]>0 and all(permissions.values()) and all(result["isolation"].values())
 result["temporary_database_removed"]=True;result.pop("temporary_database",None);print(json.dumps(result,default=str))
 admin.dispose();preview.dispose()
finally:
 with cluster.connect() as db:
  db.execute(text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=:name"),{"name":name});db.exec_driver_sql('DROP DATABASE IF EXISTS "'+name+'"')
 cluster.dispose();dump.unlink(missing_ok=True)
'''
        setup = (
            f"set -a; source {q(project + '/.secrets/p1_9_demo.env')}; set +a; "
            f"export PROJECT={q(project)} POSTGRES_BIN={q(root + '/conda/envs/postgresql18/bin')} PYTHONPATH={q(project + '/src')} APP_ENV=p1_10_test DISCOVERY_QUARANTINE_ROOT={q(project + '/storage/p1_9_demo/quarantine/sha256')}"
        )
        result = _remote_json(client, python, setup, code, timeout=5400)
        print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
        if not result.get("pass"):
            raise SystemExit(1)
    finally:
        client.close()


def quality_action() -> None:
    client, _gate = p19.a100.connect()
    try:
        root, project = p19.a100.paths(client)
        python = root + "/conda/envs/epidemic-core/bin/python"
        cli = project + "/scripts/openclaw/openclaw_cli.sh"

        def check(label: str, command: str, timeout: int) -> dict[str, object]:
            started = time.monotonic()
            rc, output, error = p19.a100.run(client, f"bash -c {q(command)}", timeout=timeout)
            safe = (output + "\n" + error).replace(project, "[project]").replace(root, "[data-root]")
            safe = re.sub(r"postgresql(?:\+\w+)?://[^\s]+", "[DATABASE_URL_REDACTED]", safe)
            safe = re.sub(
                r"(?i)(password|token|secret|api[_-]?key)(\s*[=:]\s*)\S+", r"\1\2[REDACTED]", safe
            )
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
            _isolated_pytest_result(client, root, project),
            check("ruff_check", f"cd {q(project)}; {q(python)} -m ruff check src tests migrations", 1200),
            check("ruff_format", f"cd {q(project)}; {q(python)} -m ruff format --check src tests migrations", 1200),
            check("mypy_strict", f"cd {q(project)}; {q(python)} -m mypy --strict src", 1800),
            check("pip_check", f"cd {q(project)}; {q(python)} -m pip check", 600),
            check("bash_syntax", f"bash -n {q(project + '/scripts/preview_publication_worker/')}*.sh {q(project + '/scripts/source_review/install_runtime.sh')} {q(project + '/scripts/dashboard/_common.sh')} {q(project + '/scripts/dashboard/install_runtime.sh')}", 300),
            check("alembic_single_head", f"cd {q(project)}; {q(python)} -m alembic -c alembic.ini heads", 600),
            check("npm_build", f"{npm_prefix}; {q(npm)} run build", 1200),
            check("npm_test", f"{npm_prefix}; {q(npm)} test", 1200),
            check("npm_audit", f"{npm_prefix}; {q(npm)} audit --json --registry=https://registry.npmjs.org --fetch-retries=1 --fetch-timeout=60000", 1200),
            check("openclaw_doctor_lint", f"{q(cli)} doctor --lint --json", 600),
            check("openclaw_doctor_deep", f"{q(cli)} doctor --deep --non-interactive", 900),
            check("openclaw_security_deep", f"{q(cli)} security audit --deep --json", 900),
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
formal_url=make_url(os.environ["MIGRATION_DATABASE_URL"]).set(username="epidemicdb",password=os.environ["POSTGRES_ADMIN_PASSWORD"]);formal=sa.create_engine(formal_url,hide_parameters=True)
with formal.connect() as db:
 database={"migration":db.execute(text("SELECT version_num FROM epidemic.alembic_version")).scalar_one(),"preview_decisions":db.execute(text("SELECT count(*) FROM epidemic_preview.preview_review_decisions")).scalar_one(),"preview_jobs":db.execute(text("SELECT count(*) FROM epidemic_preview.preview_publication_jobs")).scalar_one(),"preview_publications":db.execute(text("SELECT count(*) FROM epidemic_preview.preview_dashboard_publications")).scalar_one(),"preview_links":db.execute(text("SELECT count(*) FROM epidemic_discovery.discovery_run_candidate_links")).scalar_one(),"trusted_observations":db.execute(text("SELECT count(*) FROM epidemic.normalized_observations")).scalar_one(),"trusted_snapshots":db.execute(text("SELECT count(*) FROM epidemic.dataset_snapshots")).scalar_one(),"training":db.execute(text("SELECT count(*) FROM epidemic.training_snapshot_observations_v1")).scalar_one(),"daily_schedules":db.execute(text("SELECT count(*) FROM epidemic.daily_update_schedules")).scalar_one(),"candidate":dict(db.execute(text("SELECT c.status,rc.state,(SELECT count(*) FROM epidemic_discovery.source_review_decisions d WHERE d.review_case_id=rc.id AND d.decision='APPROVED') approvals,(SELECT count(*) FROM epidemic_discovery.source_review_decisions d WHERE d.review_case_id=rc.id AND d.decision='REJECTED') rejections,(SELECT count(*) FROM epidemic_discovery.source_promotion_runs p JOIN epidemic_discovery.source_review_decisions d ON d.id=p.review_decision_id WHERE d.review_case_id=rc.id) promotions FROM epidemic_discovery.source_candidates c JOIN epidemic_discovery.source_candidate_versions cv ON cv.candidate_id=c.id JOIN epidemic_discovery.source_review_cases rc ON rc.candidate_version_id=cv.id WHERE c.publisher ILIKE '%Brazil%' OR c.publisher ILIKE '%DATASUS%' ORDER BY cv.created_at DESC LIMIT 1")).mappings().one())}
 permissions={"preview_trusted_write":db.execute(text("SELECT has_table_privilege('epidemicpreview','epidemic.normalized_observations','INSERT,UPDATE,DELETE')")).scalar_one(),"dashboard_raw_read":db.execute(text("SELECT has_table_privilege('epidemic_dashboard_ro','epidemic_discovery.quarantine_artifacts','SELECT')")).scalar_one(),"review_preview_observation_write":db.execute(text("SELECT has_table_privilege('epidemicreview','epidemic_preview.preview_observations','INSERT,UPDATE,DELETE')")).scalar_one(),"daily_preview_read":db.execute(text("SELECT has_table_privilege('epidemic_app','epidemic_preview.preview_observations','SELECT')")).scalar_one()}
ports={5432,8000,8010,18789,8020,8021};listeners=[]
for filename,v6 in (("/proc/net/tcp",False),("/proc/net/tcp6",True)):
 for line in pathlib.Path(filename).read_text().splitlines()[1:]:
  fields=line.split();address,port_hex=fields[1].split(":");port=int(port_hex,16)
  if fields[3]=="0A" and port in ports:listeners.append({"port":port,"loopback":address==("00000000000000000000000001000000" if v6 else "0100007F")})
project_bytes=str(project).encode();processes=[]
for proc in pathlib.Path("/proc").iterdir():
 if not proc.name.isdigit():continue
 try:cmd=(proc/"cmdline").read_bytes();status=(proc/"status").read_text();env=(proc/"environ").read_bytes()
 except Exception:continue
 if project_bytes not in cmd:continue
 uid=int(next(line.split()[1] for line in status.splitlines() if line.startswith("Uid:")));names={item.split(b"=",1)[0] for item in env.split(b"\0") if b"=" in item}
 processes.append({"same_uid":uid==os.getuid(),"proxy_variables_present":bool(names&{b"http_proxy",b"https_proxy",b"HTTP_PROXY",b"HTTPS_PROXY",b"all_proxy",b"ALL_PROXY"})})
status=subprocess.run(["git","-C",str(project),"status","--porcelain=v1","-uall"],text=True,capture_output=True,check=True).stdout.splitlines();staged=subprocess.run(["git","-C",str(project),"diff","--cached","--name-only"],text=True,capture_output=True,check=True).stdout.splitlines();diff_check=subprocess.run(["git","-C",str(project),"diff","--check"],capture_output=True).returncode
print(json.dumps({"scan":{"findings":findings,"large_files":large},"database":database,"permissions":permissions,"runtime":{"listeners":listeners,"processes":processes},"git":{"dirty_count":len(status),"staged_paths":staged,"diff_check_returncode":diff_check}}))
'''
        setup = (
            f"source {q(project + '/scripts/database/_env.sh')}; set -a; source {q(project + '/.secrets/postgres.env')}; set +a; "
            f"export PROJECT={q(project)} FILES={q(json.dumps(P1_10_FILES))}"
        )
        audit = _remote_json(client, python, setup, code, timeout=600)
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
            name: p19.a100.run(client, q(project + "/" + relative), timeout=180)[0] == 0
            for name, relative in service_paths.items()
        }
        required = {"pytest_full_isolated", "ruff_check", "ruff_format", "mypy_strict", "pip_check", "bash_syntax", "alembic_single_head", "npm_build", "npm_test", "npm_audit"}
        command_pass = all(row["returncode"] == 0 for row in checks if row["label"] in required)
        db = audit["database"]
        permission_pass = not any(audit["permissions"].values())
        pass_value = (
            command_pass
            and not audit["scan"]["findings"]
            and not audit["scan"]["large_files"]
            and db["migration"] == "0018_p1_10_preview_publication"
            and not any(db[key] for key in ("preview_decisions", "preview_jobs", "preview_publications", "preview_links"))
            and db["candidate"]["state"] == "REVIEW_READY"
            and not any(db["candidate"][key] for key in ("approvals", "rejections", "promotions"))
            and permission_pass
            and all(item["loopback"] for item in audit["runtime"]["listeners"])
            and all(item["same_uid"] and not item["proxy_variables_present"] for item in audit["runtime"]["processes"])
            and audit["git"]["diff_check_returncode"] == 0
            and not audit["git"]["staged_paths"]
            and all(services.values())
        )
        result = {"checks": checks, "audit": audit, "services": services, "pass": pass_value}
        print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
        if not pass_value:
            raise SystemExit(1)
    finally:
        client.close()


def refresh_runtime_action() -> None:
    client, _gate = p19.a100.connect()
    try:
        _root, project = p19.a100.paths(client)
        for relative in (
            "scripts/source_review/stop_source_review.sh",
            "scripts/dashboard/stop_dashboard.sh",
            "scripts/source_discovery_worker/stop_source_discovery_worker.sh",
            "scripts/preview_publication_worker/stop_preview_publication_worker.sh",
        ):
            p19.a100.run(client, q(project + "/" + relative), timeout=180)
        p19.a100.checked(client, q(project + "/scripts/source_review/install_runtime.sh"), timeout=180)
        p19.a100.checked(client, q(project + "/scripts/dashboard/install_runtime.sh"), timeout=180)
        results: dict[str, bool] = {}
        for relative in (
            "scripts/source_discovery_worker/start_source_discovery_worker.sh",
            "scripts/source_review/start_source_review.sh",
            "scripts/dashboard/start_dashboard.sh",
            "scripts/preview_publication_worker/start_preview_publication_worker.sh",
        ):
            results[relative] = p19.a100.run(client, q(project + "/" + relative), timeout=240)[0] == 0
        if not all(results.values()):
            raise RuntimeError("P1_10_RUNTIME_REFRESH_FAILED")
        print(json.dumps({"refreshed": True, "services": results}, indent=2, sort_keys=True))
    finally:
        client.close()


def pytest_diagnostic_action() -> None:
    client, _gate = p19.a100.connect()
    try:
        root, project = p19.a100.paths(client)
        python = root + "/conda/envs/epidemic-core/bin/python"
        command = (
            f"cd {q(project)}; PYTHONPATH=src {q(python)} -m pytest -p no:cacheprovider "
            "tests/integration/test_postgres.py::test_reference_counts_and_chinese_resolution -vv --tb=long"
        )
        rc, output, error = p19.a100.run(client, f"bash -c {q(command)}", timeout=600)
        safe = (output + "\n" + error).replace(project, "[project]").replace(root, "[data-root]")
        safe = re.sub(r"postgresql(?:\+\w+)?://[^\s]+", "[DATABASE_URL_REDACTED]", safe)
        safe = re.sub(r"(?i)(password|token|secret|api[_-]?key)(\s*[=:]\s*)\S+", r"\1\2[REDACTED]", safe)
        print(json.dumps({"returncode": rc, "tail": safe.splitlines()[-100:]}, ensure_ascii=True, indent=2))
    finally:
        client.close()


def test_env_inventory_action() -> None:
    client, _gate = p19.a100.connect()
    try:
        _root, project = p19.a100.paths(client)
        command = (
            f"cd {q(project)}; grep -R -n -E "
            + q("MIGRATION_DATABASE_URL|TEST_DATABASE_URL|CREATE DATABASE|DROP SCHEMA")
            + " tests conftest.py scripts/database 2>/dev/null"
        )
        rc, output, error = p19.a100.run(client, f"bash -c {q(command)}", timeout=300)
        safe = (output + "\n" + error).replace(project, "[project]")
        print(json.dumps({"returncode": rc, "lines": safe.splitlines()[:300]}, ensure_ascii=True, indent=2))
    finally:
        client.close()


def launcher_gate_action() -> None:
    client, gate = p19.a100.connect()
    try:
        root, project = p19.a100.paths(client)
        python = root + "/conda/envs/epidemic-core/bin/python"
        code = r'''import json,os,pathlib,subprocess
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import make_url
project=pathlib.Path(os.environ["PROJECT"]);url=make_url(os.environ["MIGRATION_DATABASE_URL"]).set(username="epidemicdb",password=os.environ["POSTGRES_ADMIN_PASSWORD"])
engine=sa.create_engine(url,hide_parameters=True)
with engine.connect() as db:
 migration=db.execute(text("SELECT version_num FROM epidemic.alembic_version")).scalar_one()
 marker=db.execute(text("SELECT environment='production' AND production AND NOT disposable AND database_name=current_database() FROM epidemic_preview.environment_markers WHERE environment='production'")).scalar_one_or_none()
subject=subprocess.run(["git","-C",str(project),"show","-s","--format=%s","HEAD"],text=True,capture_output=True,check=True).stdout.strip()
proxy_names={name for name in ("http_proxy","https_proxy","HTTP_PROXY","HTTPS_PROXY","all_proxy","ALL_PROXY") if os.environ.get(name)}
print(json.dumps({"migration":migration,"production_marker":bool(marker),"commit_subject_match":subject=="feat(p1-10): add formal candidate preview publication tier","proxy_variables_present":bool(proxy_names)}))
'''
        setup = (
            f"source {q(project + '/scripts/database/_env.sh')}; set -a; source {q(project + '/.secrets/postgres.env')}; set +a; "
            f"export PROJECT={q(project)}"
        )
        result = _remote_json(client, python, setup, code, timeout=300)
        result["target_identity_verified"] = bool(gate.get("stable_second_connection"))
        passed = (
            result["migration"] == "0018_p1_10_preview_publication"
            and result["production_marker"]
            and result["commit_subject_match"]
            and not result["proxy_variables_present"]
            and result["target_identity_verified"]
        )
        print(json.dumps({"pass": passed, "gate": result}, ensure_ascii=True, indent=2, sort_keys=True))
        if not passed:
            raise SystemExit(1)
    finally:
        client.close()


def ui_demo_action(start: bool) -> None:
    client, _gate = p19.a100.connect()
    try:
        root, project = p19.a100.paths(client)
        python = root + "/conda/envs/epidemic-core/bin/python"
        pidfile = project + "/storage/p1_9_demo/run/p1_10_dashboard.pid"
        logfile = project + "/storage/p1_9_demo/log/p1_10_dashboard.log"
        if not start:
            code = r'''import os,pathlib,signal
pidfile=pathlib.Path(os.environ["PIDFILE"]);stopped=False
if pidfile.exists():
 try:
  pid=int(pidfile.read_text().strip());cmd=pathlib.Path(f"/proc/{pid}/cmdline").read_bytes()
  if b"epidemic_core.dashboard.app:create_app" in cmd:os.kill(pid,signal.SIGTERM);stopped=True
 except (ValueError,ProcessLookupError,FileNotFoundError):pass
 pidfile.unlink(missing_ok=True)
print("stopped" if stopped else "not-running")
'''
            rc, output, _error = p19.a100.run(
                client, f"PIDFILE={q(pidfile)} {q(python)} -c {q(code)}", timeout=120
            )
            print(json.dumps({"stopped": rc == 0 and output.strip() in {"stopped", "not-running"}}))
            return
        stop_code = r'''import os,pathlib,signal
p=pathlib.Path(os.environ["PIDFILE"])
if p.exists():
 try:
  pid=int(p.read_text().strip());cmd=pathlib.Path(f"/proc/{pid}/cmdline").read_bytes()
  if b"epidemic_core.dashboard.app:create_app" in cmd:os.kill(pid,signal.SIGTERM)
 except (ValueError,ProcessLookupError,FileNotFoundError):pass
p.unlink(missing_ok=True)
'''
        p19.a100.run(client, f"PIDFILE={q(pidfile)} {q(python)} -c {q(stop_code)}", timeout=120)
        command = (
            f"source {q(project + '/scripts/p1_9_demo/_common.sh')}; set -a; source \"$SECRETS\"; set +a; "
            f"export DASHBOARD_DATABASE_URL=\"$DEMO_DASHBOARD_DATABASE_URL\" DASHBOARD_AUTH_FILE=\"$AUTH_FILE\" "
            f"DASHBOARD_PUBLIC_ORIGIN=http://127.0.0.1:8124 PYTHONPATH={q(project + '/src')}; "
            "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; "
            f"nohup {q(python)} -m uvicorn epidemic_core.dashboard.app:create_app --factory --host 127.0.0.1 --port 18024 --no-access-log >>{q(logfile)} 2>&1 & echo $! >{q(pidfile)}"
        )
        p19.a100.checked(client, f"bash -c {q(command)}", timeout=120)
        healthy = False
        for _ in range(30):
            rc, _output, _error = p19.a100.run(
                client, "curl --fail --silent --show-error --max-time 3 http://127.0.0.1:18024/healthz", timeout=10
            )
            if rc == 0:
                healthy = True
                break
            time.sleep(0.5)
        print(json.dumps({"started": healthy, "loopback": True, "remote_port": 18024}))
        if not healthy:
            raise SystemExit(1)
    finally:
        client.close()


def ui_demo_log_action() -> None:
    client, _gate = p19.a100.connect()
    try:
        root, project = p19.a100.paths(client)
        logfile = project + "/storage/p1_9_demo/log/p1_10_dashboard.log"
        rc, output, error = p19.a100.run(client, f"tail -n 100 {q(logfile)}", timeout=120)
        safe = (output + error).replace(project, "[project]").replace(root, "[data-root]")
        safe = re.sub(r"postgresql(?:\+\w+)?://[^\s]+", "[DATABASE_URL_REDACTED]", safe)
        safe = re.sub(r"(?i)(password|token|secret|api[_-]?key)(\s*[=:]\s*)\S+", r"\1\2[REDACTED]", safe)
        print(json.dumps({"returncode": rc, "tail": safe.splitlines()[-100:]}, ensure_ascii=True, indent=2))
    finally:
        client.close()


def report_inventory_action() -> None:
    client, _gate = p19.a100.connect()
    try:
        root, project = p19.a100.paths(client)
        python = root + "/conda/envs/epidemic-core/bin/python"
        code = r'''import hashlib,json,os,pathlib
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import make_url
project=pathlib.Path(os.environ["PROJECT"])
url=make_url(os.environ["MIGRATION_DATABASE_URL"]).set(username="epidemicdb",password=os.environ["POSTGRES_ADMIN_PASSWORD"])
engine=sa.create_engine(url,hide_parameters=True)
with engine.connect() as db:
 relations=[dict(row) for row in db.execute(text("""SELECT c.relname AS name,CASE c.relkind WHEN 'r' THEN 'table' WHEN 'v' THEN 'view' WHEN 'm' THEN 'materialized_view' WHEN 'S' THEN 'sequence' ELSE c.relkind::text END AS kind FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='epidemic_preview' AND c.relkind IN ('r','v','m','S') ORDER BY kind,name""")).mappings()]
 counts={}
 for row in relations:
  if row["kind"]=="table": counts[row["name"]]=db.execute(text('SELECT count(*) FROM epidemic_preview."'+row["name"]+'"')).scalar_one()
 marker=dict(db.execute(text("SELECT environment,production,disposable,database_name FROM epidemic_preview.environment_markers WHERE environment='production'")).mappings().one())
backups=[]
for path in sorted((project/"storage/backups/p1_10").rglob("*.dump")):
 digest=hashlib.sha256()
 with path.open("rb") as handle:
  for block in iter(lambda:handle.read(1024*1024),b""):digest.update(block)
 backups.append({"path":"[project]/"+path.relative_to(project).as_posix(),"bytes":path.stat().st_size,"sha256":digest.hexdigest()})
print(json.dumps({"relations":relations,"counts":counts,"production_marker":marker,"backups":backups},default=str))
'''
        setup = (
            f"source {q(project + '/scripts/database/_env.sh')}; set -a; source {q(project + '/.secrets/postgres.env')}; set +a; "
            f"export PROJECT={q(project)}"
        )
        result = _remote_json(client, python, setup, code, timeout=600)
        print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
    finally:
        client.close()


def final_status_action() -> None:
    client, _gate = p19.a100.connect()
    try:
        root, project = p19.a100.paths(client)
        python = root + "/conda/envs/epidemic-core/bin/python"
        code = r'''import json,os
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import make_url
url=make_url(os.environ["MIGRATION_DATABASE_URL"]).set(username="epidemicdb",password=os.environ["POSTGRES_ADMIN_PASSWORD"])
engine=sa.create_engine(url,hide_parameters=True)
with engine.connect() as db:
 result={
  "migration":db.execute(text("SELECT version_num FROM epidemic.alembic_version")).scalar_one(),
  "trusted_observations":db.execute(text("SELECT count(*) FROM epidemic.normalized_observations")).scalar_one(),
  "trusted_snapshots":db.execute(text("SELECT count(*) FROM epidemic.dataset_snapshots")).scalar_one(),
  "trusted_snapshot_members":db.execute(text("SELECT count(*) FROM epidemic.dataset_snapshot_observations")).scalar_one(),
  "training_rows":db.execute(text("SELECT count(*) FROM epidemic.training_snapshot_observations_v1")).scalar_one(),
  "daily_schedules":db.execute(text("SELECT count(*) FROM epidemic.daily_update_schedules")).scalar_one(),
  "preview_decisions":db.execute(text("SELECT count(*) FROM epidemic_preview.preview_review_decisions")).scalar_one(),
  "preview_jobs":db.execute(text("SELECT count(*) FROM epidemic_preview.preview_publication_jobs")).scalar_one(),
  "preview_publications":db.execute(text("SELECT count(*) FROM epidemic_preview.preview_dashboard_publications")).scalar_one(),
  "preview_observations":db.execute(text("SELECT count(*) FROM epidemic_preview.preview_observations")).scalar_one(),
  "candidate_state":db.execute(text("SELECT state FROM epidemic_discovery.source_review_cases WHERE id='a0cc01f7-5ea3-4d07-b74a-d4e65d3ca9ea'")).scalar_one(),
  "trusted_approvals":db.execute(text("SELECT count(*) FROM epidemic_discovery.source_review_decisions WHERE review_case_id='a0cc01f7-5ea3-4d07-b74a-d4e65d3ca9ea'")).scalar_one()
 }
print(json.dumps(result))
'''
        setup = f"source {q(project + '/scripts/database/_env.sh')}; set -a; source {q(project + '/.secrets/postgres.env')}; set +a"
        result = _remote_json(client, python, setup, code)
        result["git_head"] = p19.a100.checked(client, f"git -C {q(project)} rev-parse HEAD")
        result["git_subject"] = p19.a100.checked(client, f"git -C {q(project)} show -s --format=%s HEAD")
        result["commit_path_count"] = len(p19.a100.checked(client, f"git -C {q(project)} diff-tree --no-commit-id --name-only -r HEAD").splitlines())
        result["staged_paths"] = len(p19.a100.checked(client, f"git -C {q(project)} diff --cached --name-only").splitlines())
        result["preexisting_dirty_paths"] = len(p19.a100.checked(client, f"git -C {q(project)} status --porcelain=v2 -uall").splitlines())
        print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
    finally:
        client.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action",
        choices=("snapshot", "baseline", "download-head", "upload", "upload-reports", "commit", "repair-index", "validate", "format", "migration-test", "prepare-demo", "isolated-e2e", "isolated-diagnostics", "formal-deploy", "quality", "refresh-runtime", "pytest-diagnostic", "test-env-inventory", "isolated-pytest", "contract-tests", "launcher-gate", "start-ui-demo", "stop-ui-demo", "ui-demo-log", "report-inventory", "final-status"),
    )
    args = parser.parse_args()
    if args.action == "snapshot":
        snapshot_action()
    elif args.action == "baseline":
        baseline_action()
    elif args.action == "download-head":
        download_head_action()
    elif args.action == "upload":
        upload_action()
    elif args.action == "upload-reports":
        upload_reports_action()
    elif args.action == "commit":
        commit_action()
    elif args.action == "repair-index":
        repair_index_action()
    elif args.action == "validate":
        validate_action()
    elif args.action == "format":
        format_action()
    elif args.action == "migration-test":
        migration_test_action()
    elif args.action == "prepare-demo":
        prepare_demo_action()
    elif args.action == "isolated-e2e":
        isolated_e2e_action()
    elif args.action == "formal-deploy":
        formal_deploy_action()
    elif args.action == "quality":
        quality_action()
    elif args.action == "refresh-runtime":
        refresh_runtime_action()
    elif args.action == "pytest-diagnostic":
        pytest_diagnostic_action()
    elif args.action == "test-env-inventory":
        test_env_inventory_action()
    elif args.action == "isolated-pytest":
        isolated_pytest_action()
    elif args.action == "contract-tests":
        contract_tests_action()
    elif args.action == "launcher-gate":
        launcher_gate_action()
    elif args.action == "start-ui-demo":
        ui_demo_action(True)
    elif args.action == "stop-ui-demo":
        ui_demo_action(False)
    elif args.action == "ui-demo-log":
        ui_demo_log_action()
    elif args.action == "report-inventory":
        report_inventory_action()
    elif args.action == "final-status":
        final_status_action()
    else:
        isolated_diagnostics_action()


if __name__ == "__main__":
    main()
