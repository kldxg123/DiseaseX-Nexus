from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import a100_p1_11 as p111


ROOT = Path(__file__).resolve().parents[1]
LOCAL_REPORT_DIR = ROOT / "reports" / "p1_15_ebola_cod_trusted_expansion"
REMOTE_REPORT_REL = "reports/p1_15_ebola_cod_trusted_expansion"
LOCAL_REPORT_DIR_P1_15B = ROOT / "reports" / "p1_15b_source_contract_parser_dry_run"
REMOTE_REPORT_REL_P1_15B = "reports/p1_15b_source_contract_parser_dry_run"
LOCAL_REPORT_DIR_P1_15B_R2 = ROOT / "reports" / "p1_15b_r2_don398_parser_review_case"
REMOTE_REPORT_REL_P1_15B_R2 = "reports/p1_15b_r2_don398_parser_review_case"
LOCAL_REPORT_DIR_EBOLA_LOOP = ROOT / "reports" / "ebola_data_visualization_prediction_engineering_loop"
REMOTE_REPORT_REL_EBOLA_LOOP = "reports/ebola_data_visualization_prediction_engineering_loop"
LOCAL_REPORT_DIR_OFFICIAL_LOOP = ROOT / "reports" / "ebola_official_trusted_review_summary_prediction_demo"
REMOTE_REPORT_REL_OFFICIAL_LOOP = "reports/ebola_official_trusted_review_summary_prediction_demo"
LOCAL_REPORT_DIR_P1_16 = ROOT / "reports" / "ebola_historical_and_real_influenza_region_week_pipeline"
REMOTE_REPORT_REL_P1_16 = "reports/ebola_historical_and_real_influenza_region_week_pipeline"
LOCAL_REPORT_DIR_P1_16R_A = ROOT / "reports" / "p1_16r_ebola_source_acquisition"
REMOTE_REPORT_REL_P1_16R_A = "reports/p1_16r_ebola_source_acquisition"
LOCAL_REPORT_DIR_P1_17A_R = ROOT / "reports" / "p1_17a_flunet_prediction_mvp_repair"
REMOTE_REPORT_REL_P1_17A_R = "reports/p1_17a_flunet_prediction_mvp_repair"
LOCAL_REPORT_DIR_FASTTRACK = ROOT / "reports" / "epidemic_fasttrack_e2e"


def sanitize(text: str) -> str:
    text = re.sub(r"(?i)(password|token|secret|api[_-]?key)=\\S+", r"\\1=[redacted]", text)
    text = re.sub(r"postgresql(\\+\\w+)?://[^\\s'\\\"]+", "postgresql://[redacted]", text)
    return text


def remote_run(client, command: str, timeout: int = 300) -> str:
    return p111.p110r3.previous.p19.a100.checked(client, command, timeout=timeout)


def create_plan() -> None:
    client, _gate = p111.p110r3.previous.p19.a100.connect()
    try:
        _data_root, project = p111.p110r3.previous.p19.a100.paths(client)
        plan = """# P1-15 Execution Plan

Status: EXECUTION_STARTED

Scope:
- Expand COD + Ebola official historical evidence if and only if source contracts, parser semantics, provenance, deduplication and approval gates permit it.
- Keep P1-14 deletion/remediation chain paused; do not run retry3, P1-14F, query_requests cleanup or DELETE SQL generation.
- Preserve existing P1-14 evidence directories untouched.
- Use only official public-health sources for candidate evidence.
- Do not use Qwen/vLLM/LLM extraction, general web search, Brave, news, papers, blogs or inferred values as Trusted data.

Execution order:
1. Read applicable project guidance and inspect current repository/runtime/database state.
2. Record baseline.json, database_before.json and runtime_before.json.
3. Reconstruct the current COD/Ebola Trusted path and Dashboard read path.
4. Verify official WHO/WHO AFRO source pages and prepare an outbreak coverage matrix.
5. Determine whether all new source families/parser versions already have real human approval.
6. If approval is absent, emit APPROVAL_CHECKPOINT.md and stop at P1_15_AWAITING_HUMAN_SOURCE_APPROVAL.
7. Only if approval already exists, run a dry-run delta, then a scoped Trusted promotion with exact before/after validation.

Safety gates:
- rows deleted must remain 0.
- existing Trusted values modified must remain 0 unless a separately approved correction path exists.
- non-COD and non-Ebola writes must remain 0.
- Dashboard remains read-only and loopback.
- Services may not be restarted unless tests pass and reload is necessary for this stage.
"""
        command = (
            f"cd {p111.q(project)}; "
            f"mkdir -p {p111.q(REMOTE_REPORT_REL + '/screenshots')}; "
            f"python3 -c {p111.q('from pathlib import Path; Path(' + repr(REMOTE_REPORT_REL + '/EXECUTION_PLAN.md') + ').write_text(' + repr(plan) + ', encoding=\"utf-8\")')}"
        )
        remote_run(client, command)
        print(json.dumps({"created": f"{REMOTE_REPORT_REL}/EXECUTION_PLAN.md"}, ensure_ascii=False))
    finally:
        client.close()


def download_reports(report_rel: str = REMOTE_REPORT_REL, local_report_dir: Path = LOCAL_REPORT_DIR, zip_names: list[str] | None = None) -> None:
    if zip_names is None:
        zip_names = ["p1_15_ebola_cod_trusted_expansion_complete.zip", "p1_15_ebola_cod_trusted_expansion_complete.zip.sha256"]
    client, _gate = p111.p110r3.previous.p19.a100.connect()
    try:
        _data_root, project = p111.p110r3.previous.p19.a100.paths(client)
        if local_report_dir.exists():
            shutil.rmtree(local_report_dir)
        local_report_dir.mkdir(parents=True)
        listing = remote_run(
            client,
            "python3 -c "
            + p111.q(
                "from pathlib import Path; import json; "
                f"root=Path({project!r})/{report_rel!r}; "
                "print(json.dumps([str(p.relative_to(root)) for p in root.rglob('*') if p.is_file()]))"
            ),
        )
        names = json.loads(listing)
        sftp = client.open_sftp()
        try:
            for name in names:
                target = local_report_dir / name
                target.parent.mkdir(parents=True, exist_ok=True)
                sftp.get(f"{project}/{report_rel}/{name}", str(target))
            for name in zip_names:
                try:
                    sftp.get(f"{project}/reports/{name}", str(ROOT / "reports" / name))
                except OSError:
                    pass
        finally:
            sftp.close()
        print(json.dumps({"local_report_dir": str(local_report_dir), "files": len(names)}, ensure_ascii=False))
    finally:
        client.close()


def download_latest_p1_18() -> None:
    client, _gate = p111.p110r3.previous.p19.a100.connect()
    try:
        _data_root, project = p111.p110r3.previous.p19.a100.paths(client)
        listing = remote_run(
            client,
            "python3 -c "
            + p111.q(
                "from pathlib import Path; import json; "
                f"root=Path({project!r})/'artifacts'; "
                "dirs=sorted([p for p in root.glob('p1_18_flunet_deep_forecast_*') if p.is_dir()], key=lambda p:p.stat().st_mtime); "
                "latest=dirs[-1]; "
                "print(json.dumps({'name':latest.name,'files':[str(p.relative_to(latest)) for p in latest.rglob('*') if p.is_file()]}))"
            ),
        )
        payload = json.loads(listing)
        name = payload["name"]
        local_dir = ROOT / "artifacts" / name
        if local_dir.exists():
            shutil.rmtree(local_dir)
        local_dir.mkdir(parents=True)
        sftp = client.open_sftp()
        try:
            for rel in payload["files"]:
                target = local_dir / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                sftp.get(f"{project}/artifacts/{name}/{rel}", str(target))
            for suffix in [".zip", ".zip.sha256"]:
                remote = f"{project}/reports/{name}{suffix}"
                local = ROOT / "reports" / f"{name}{suffix}"
                local.parent.mkdir(parents=True, exist_ok=True)
                try:
                    sftp.get(remote, str(local))
                except OSError:
                    pass
        finally:
            sftp.close()
        print(json.dumps({"local_artifact_dir": str(local_dir), "files": len(payload["files"])}, ensure_ascii=False))
    finally:
        client.close()


def download_latest_p1_18_r0() -> None:
    client, _gate = p111.p110r3.previous.p19.a100.connect()
    try:
        _data_root, project = p111.p110r3.previous.p19.a100.paths(client)
        listing = remote_run(
            client,
            "python3 -c "
            + p111.q(
                "from pathlib import Path; import json; "
                f"root=Path({project!r})/'artifacts'; "
                "dirs=sorted([p for p in root.glob('p1_18_r0_postgres_cpu_runtime_*') if p.is_dir()], key=lambda p:p.stat().st_mtime); "
                "latest=dirs[-1]; "
                "print(json.dumps({'name':latest.name,'files':[str(p.relative_to(latest)) for p in latest.rglob('*') if p.is_file()]}))"
            ),
        )
        payload = json.loads(listing)
        name = payload["name"]
        local_dir = ROOT / "artifacts" / name
        if local_dir.exists():
            shutil.rmtree(local_dir)
        local_dir.mkdir(parents=True)
        sftp = client.open_sftp()
        try:
            for rel in payload["files"]:
                target = local_dir / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                sftp.get(f"{project}/artifacts/{name}/{rel}", str(target))
            for suffix in [".zip", ".zip.sha256"]:
                remote = f"{project}/reports/{name}{suffix}"
                local = ROOT / "reports" / f"{name}{suffix}"
                local.parent.mkdir(parents=True, exist_ok=True)
                try:
                    sftp.get(remote, str(local))
                except OSError:
                    pass
        finally:
            sftp.close()
        print(json.dumps({"local_artifact_dir": str(local_dir), "files": len(payload["files"])}, ensure_ascii=False))
    finally:
        client.close()


def download_latest_p1_18_r0a() -> None:
    client, _gate = p111.p110r3.previous.p19.a100.connect()
    try:
        _data_root, project = p111.p110r3.previous.p19.a100.paths(client)
        listing = remote_run(
            client,
            "python3 -c "
            + p111.q(
                "from pathlib import Path; import json; "
                f"root=Path({project!r})/'artifacts'; "
                "dirs=sorted([p for p in root.glob('p1_18_r0a_postgres_identity_audit_*') if p.is_dir()], key=lambda p:p.stat().st_mtime); "
                "latest=dirs[-1]; "
                "print(json.dumps({'name':latest.name,'files':[str(p.relative_to(latest)) for p in latest.rglob('*') if p.is_file()]}))"
            ),
        )
        payload = json.loads(listing)
        name = payload["name"]
        local_dir = ROOT / "artifacts" / name
        if local_dir.exists():
            shutil.rmtree(local_dir)
        local_dir.mkdir(parents=True)
        sftp = client.open_sftp()
        try:
            for rel in payload["files"]:
                target = local_dir / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                sftp.get(f"{project}/artifacts/{name}/{rel}", str(target))
            for suffix in [".zip", ".zip.sha256"]:
                remote = f"{project}/reports/{name}{suffix}"
                local = ROOT / "reports" / f"{name}{suffix}"
                local.parent.mkdir(parents=True, exist_ok=True)
                try:
                    sftp.get(remote, str(local))
                except OSError:
                    pass
        finally:
            sftp.close()
        print(json.dumps({"local_artifact_dir": str(local_dir), "files": len(payload["files"])}, ensure_ascii=False))
    finally:
        client.close()


def download_latest_p1_18_r0a1() -> None:
    client, _gate = p111.p110r3.previous.p19.a100.connect()
    try:
        _data_root, project = p111.p110r3.previous.p19.a100.paths(client)
        listing = remote_run(
            client,
            "python3 -c "
            + p111.q(
                "from pathlib import Path; import json; "
                f"root=Path({project!r})/'artifacts'; "
                "dirs=sorted([p for p in root.glob('p1_18_r0a1_ahr1004_contract_preflight_*') if p.is_dir()], key=lambda p:p.stat().st_mtime); "
                "latest=dirs[-1]; "
                "print(json.dumps({'name':latest.name,'files':[str(p.relative_to(latest)) for p in latest.rglob('*') if p.is_file()]}))"
            ),
        )
        payload = json.loads(listing)
        name = payload["name"]
        local_dir = ROOT / "artifacts" / name
        if local_dir.exists():
            shutil.rmtree(local_dir)
        local_dir.mkdir(parents=True)
        sftp = client.open_sftp()
        try:
            for rel in payload["files"]:
                target = local_dir / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                sftp.get(f"{project}/artifacts/{name}/{rel}", str(target))
            for suffix in [".zip", ".zip.sha256"]:
                remote = f"{project}/reports/{name}{suffix}"
                local = ROOT / "reports" / f"{name}{suffix}"
                local.parent.mkdir(parents=True, exist_ok=True)
                try:
                    sftp.get(remote, str(local))
                except OSError:
                    pass
        finally:
            sftp.close()
        print(json.dumps({"local_artifact_dir": str(local_dir), "files": len(payload["files"])}, ensure_ascii=False))
    finally:
        client.close()


def download_latest_fasttrack() -> None:
    client, _gate = p111.p110r3.previous.p19.a100.connect()
    try:
        _data_root, project = p111.p110r3.previous.p19.a100.paths(client)
        listing = remote_run(
            client,
            "python3 -c "
            + p111.q(
                "from pathlib import Path; import json; "
                f"project=Path({project!r}); "
                "roots=[project/'reports', project/'artifacts']; "
                "dirs=[]; "
                "[(dirs.extend([p for p in root.glob('epidemic_fasttrack_e2e_*') if p.is_dir()] + [p for p in root.glob('epidemic_fasttrack_correctness_repair_*') if p.is_dir()])) for root in roots if root.exists()]; "
                "dirs=sorted(dirs, key=lambda p:p.stat().st_mtime); "
                "latest=dirs[-1]; "
                "print(json.dumps({'base':str(latest.parent.relative_to(project)),'name':latest.name,'files':[str(p.relative_to(latest)) for p in latest.rglob('*') if p.is_file()]}))"
            ),
        )
        payload = json.loads(listing)
        local_dir = LOCAL_REPORT_DIR_FASTTRACK / payload["name"]
        if local_dir.exists():
            shutil.rmtree(local_dir)
        local_dir.mkdir(parents=True)
        sftp = client.open_sftp()
        try:
            for rel in payload["files"]:
                target = local_dir / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                sftp.get(f"{project}/{payload['base']}/{payload['name']}/{rel}", str(target))
            for suffix in [".zip", ".zip.sha256"]:
                remote = f"{project}/reports/{payload['name']}{suffix}"
                local = ROOT / "reports" / f"{payload['name']}{suffix}"
                local.parent.mkdir(parents=True, exist_ok=True)
                try:
                    sftp.get(remote, str(local))
                except OSError:
                    pass
        finally:
            sftp.close()
        print(json.dumps({"local_report_dir": str(local_dir), "files": len(payload["files"])}, ensure_ascii=False))
    finally:
        client.close()


def upload_p1_15b_local_updates() -> None:
    client, _gate = p111.p110r3.previous.p19.a100.connect()
    try:
        _data_root, project = p111.p110r3.previous.p19.a100.paths(client)
        remote_run(client, f"mkdir -p {p111.q(project + '/' + REMOTE_REPORT_REL_P1_15B + '/screenshots')} {p111.q(project + '/reports')}")
        files: list[tuple[Path, str]] = []
        for rel in [
            "dashboard_review_baseline_validation.json",
            "screenshots/dashboard_baseline_real.png",
            "screenshots/review_baseline_real.png",
        ]:
            files.append((LOCAL_REPORT_DIR_P1_15B / rel, f"{project}/{REMOTE_REPORT_REL_P1_15B}/{rel.replace(chr(92), '/')}"))
        for rel in [
            "p1_15b_source_contract_parser_dry_run_complete.zip",
            "p1_15b_source_contract_parser_dry_run_complete.zip.sha256",
        ]:
            files.append((ROOT / "reports" / rel, f"{project}/reports/{rel}"))
        sftp = client.open_sftp()
        try:
            for local, remote in files:
                sftp.put(str(local), remote)
        finally:
            sftp.close()
        print(json.dumps({"uploaded": len(files), "remote_report": REMOTE_REPORT_REL_P1_15B}, ensure_ascii=False))
    finally:
        client.close()


def upload_p1_15b_r2_local_updates() -> None:
    client, _gate = p111.p110r3.previous.p19.a100.connect()
    try:
        _data_root, project = p111.p110r3.previous.p19.a100.paths(client)
        remote_run(client, f"mkdir -p {p111.q(project + '/' + REMOTE_REPORT_REL_P1_15B_R2 + '/screenshots')} {p111.q(project + '/reports')}")
        files: list[tuple[Path, str]] = []
        for rel in [
            "dashboard_review_ui_validation.json",
            "FINAL_REPORT.json",
            "FINAL_REPORT.md",
            "payload_sha_manifest.json",
            "screenshots/dashboard_cod_ebola_baseline_login_blocker.png",
            "screenshots/review_case_668e3dd5_login_blocker.png",
        ]:
            files.append((LOCAL_REPORT_DIR_P1_15B_R2 / rel, f"{project}/{REMOTE_REPORT_REL_P1_15B_R2}/{rel.replace(chr(92), '/')}"))
        for rel in [
            "p1_15b_r2_don398_parser_review_case_complete.zip",
            "p1_15b_r2_don398_parser_review_case_complete.zip.sha256",
        ]:
            files.append((ROOT / "reports" / rel, f"{project}/reports/{rel}"))
        sftp = client.open_sftp()
        try:
            for local, remote in files:
                sftp.put(str(local), remote)
        finally:
            sftp.close()
        print(json.dumps({"uploaded": len(files), "remote_report": REMOTE_REPORT_REL_P1_15B_R2}, ensure_ascii=False))
    finally:
        client.close()


def run_remote_script(local_script: str, remote_name: str, timeout: int = 900) -> None:
    client, _gate = p111.p110r3.previous.p19.a100.connect()
    try:
        data_root, project = p111.p110r3.previous.p19.a100.paths(client)
        python = f"{data_root}/conda/envs/epidemic-core/bin/python"
        remote_script = f"/tmp/{remote_name}"
        sftp = client.open_sftp()
        try:
            sftp.put(str(ROOT / local_script), remote_script)
        finally:
            sftp.close()
        command = (
            f"cd {p111.q(project)}; "
            f"source {p111.q(project + '/scripts/database/_env.sh')}; set -a; "
            f"source {p111.q(project + '/.secrets/postgres.env')}; set +a; "
            "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; "
            f"PROJECT={p111.q(project)} exec {p111.q(python)} {p111.q(remote_script)}"
        )
        rc, out, err = p111.p110r3.previous.p19.a100.run(client, command, timeout=timeout)
        print(sanitize(out))
        if rc:
            print(sanitize(err), file=sys.stderr)
            raise SystemExit(rc)
    finally:
        client.close()


def run_remote_script_no_secrets(local_script: str, remote_name: str, timeout: int = 900) -> None:
    client, _gate = p111.p110r3.previous.p19.a100.connect()
    try:
        data_root, project = p111.p110r3.previous.p19.a100.paths(client)
        python = f"{data_root}/conda/envs/epidemic-core/bin/python"
        remote_script = f"/tmp/{remote_name}"
        sftp = client.open_sftp()
        try:
            sftp.put(str(ROOT / local_script), remote_script)
        finally:
            sftp.close()
        command = (
            f"cd {p111.q(project)}; "
            "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; "
            f"PROJECT={p111.q(project)} exec {p111.q(python)} {p111.q(remote_script)}"
        )
        rc, out, err = p111.p110r3.previous.p19.a100.run(client, command, timeout=timeout)
        print(sanitize(out))
        if rc:
            print(sanitize(err), file=sys.stderr)
            raise SystemExit(rc)
    finally:
        client.close()


def env_probe() -> None:
    client, _gate = p111.p110r3.previous.p19.a100.connect()
    try:
        data_root, project = p111.p110r3.previous.p19.a100.paths(client)
        python = f"{data_root}/conda/envs/epidemic-core/bin/python"
        code = """import os,json
from sqlalchemy.engine import make_url
u=make_url(os.environ["MIGRATION_DATABASE_URL"])
print(json.dumps({"admin_user":os.environ.get("POSTGRES_ADMIN_USER"),"url_user_before":u.username,"has_admin_password":bool(os.environ.get("POSTGRES_ADMIN_PASSWORD"))}, sort_keys=True))
"""
        command = (
            f"cd {p111.q(project)}; "
            f"source {p111.q(project + '/scripts/database/_env.sh')}; set -a; "
            f"source {p111.q(project + '/.secrets/postgres.env')}; set +a; "
            f"exec {p111.q(python)} -c {p111.q(code)}"
        )
        print(remote_run(client, command))
    finally:
        client.close()


def main() -> None:
    action = sys.argv[1] if len(sys.argv) > 1 else "create-plan"
    if action == "create-plan":
        create_plan()
    elif action == "baseline":
        run_remote_script("tools/p1_15_baseline_remote.py", "p1_15_baseline_remote.py")
    elif action == "sources":
        run_remote_script("tools/p1_15_source_assessment_remote.py", "p1_15_source_assessment_remote.py", timeout=900)
    elif action == "p1-15b":
        run_remote_script("tools/p1_15b_dry_run_remote.py", "p1_15b_dry_run_remote.py", timeout=1200)
    elif action == "p1-15b-r2":
        run_remote_script("tools/p1_15b_r2_remote.py", "p1_15b_r2_remote.py", timeout=1200)
    elif action == "p1-15b-r2-schema-probe":
        run_remote_script("tools/p1_15b_r2_schema_probe_remote.py", "p1_15b_r2_schema_probe_remote.py", timeout=300)
    elif action == "ebola-loop-audit":
        run_remote_script("tools/ebola_loop_audit_remote.py", "ebola_loop_audit_remote.py", timeout=600)
    elif action == "ebola-artifact-find":
        run_remote_script("tools/ebola_artifact_find_remote.py", "ebola_artifact_find_remote.py", timeout=300)
    elif action == "ebola-summary-backfill":
        run_remote_script("tools/ebola_review_summary_backfill_remote.py", "ebola_review_summary_backfill_remote.py", timeout=900)
    elif action == "ebola-summary-store-ui":
        run_remote_script("tools/ebola_summary_store_ui_remote.py", "ebola_summary_store_ui_remote.py", timeout=1800)
    elif action == "ebola-repo-probe":
        run_remote_script("tools/ebola_repo_probe_remote.py", "ebola_repo_probe_remote.py", timeout=300)
    elif action == "review-console-reload":
        run_remote_script("tools/review_console_reload_remote.py", "review_console_reload_remote.py", timeout=600)
    elif action == "prediction-smoke":
        run_remote_script("tools/prediction_smoke_remote.py", "prediction_smoke_remote.py", timeout=600)
    elif action == "dashboard-probe":
        run_remote_script("tools/dashboard_probe_remote.py", "dashboard_probe_remote.py", timeout=300)
    elif action == "dashboard-prediction-demo-patch":
        run_remote_script("tools/dashboard_prediction_demo_patch_remote.py", "dashboard_prediction_demo_patch_remote.py", timeout=600)
    elif action == "dashboard-reload":
        run_remote_script("tools/dashboard_reload_remote.py", "dashboard_reload_remote.py", timeout=600)
    elif action == "dashboard-prediction-demo-verify":
        run_remote_script("tools/dashboard_prediction_demo_verify_remote.py", "dashboard_prediction_demo_verify_remote.py", timeout=600)
    elif action == "ebola-evidence-closure":
        run_remote_script("tools/ebola_evidence_closure_remote.py", "ebola_evidence_closure_remote.py", timeout=600)
    elif action == "review-pending-detail-patch":
        run_remote_script("tools/review_pending_detail_patch_remote.py", "review_pending_detail_patch_remote.py", timeout=600)
    elif action == "pdf-summary-probe":
        run_remote_script("tools/pdf_summary_probe_remote.py", "pdf_summary_probe_remote.py", timeout=300)
    elif action == "blocked-summary-url-audit":
        run_remote_script("tools/blocked_summary_url_audit_remote.py", "blocked_summary_url_audit_remote.py", timeout=300)
    elif action == "landing-summary-backfill":
        run_remote_script("tools/landing_summary_backfill_remote.py", "landing_summary_backfill_remote.py", timeout=600)
    elif action == "cdc-stacks-landing-probe":
        run_remote_script("tools/cdc_stacks_landing_probe_remote.py", "cdc_stacks_landing_probe_remote.py", timeout=300)
    elif action == "after":
        run_remote_script("tools/p1_15_after_remote.py", "p1_15_after_remote.py")
    elif action == "env-probe":
        env_probe()
    elif action == "download":
        download_reports()
    elif action == "download-p1-15b":
        download_reports(
            REMOTE_REPORT_REL_P1_15B,
            LOCAL_REPORT_DIR_P1_15B,
            ["p1_15b_source_contract_parser_dry_run_complete.zip", "p1_15b_source_contract_parser_dry_run_complete.zip.sha256"],
        )
    elif action == "download-p1-15b-r2":
        download_reports(
            REMOTE_REPORT_REL_P1_15B_R2,
            LOCAL_REPORT_DIR_P1_15B_R2,
            ["p1_15b_r2_don398_parser_review_case_complete.zip", "p1_15b_r2_don398_parser_review_case_complete.zip.sha256"],
        )
    elif action == "download-ebola-loop":
        download_reports(
            REMOTE_REPORT_REL_EBOLA_LOOP,
            LOCAL_REPORT_DIR_EBOLA_LOOP,
            [],
        )
    elif action == "download-official-loop":
        download_reports(
            REMOTE_REPORT_REL_OFFICIAL_LOOP,
            LOCAL_REPORT_DIR_OFFICIAL_LOOP,
            [
                "ebola_official_trusted_review_summary_prediction_demo.zip",
                "ebola_official_trusted_review_summary_prediction_demo.zip.sha256",
            ],
        )
    elif action == "download-p1-16":
        download_reports(
            REMOTE_REPORT_REL_P1_16,
            LOCAL_REPORT_DIR_P1_16,
            [
                "ebola_historical_and_real_influenza_region_week_pipeline.zip",
                "ebola_historical_and_real_influenza_region_week_pipeline.zip.sha256",
            ],
        )
    elif action == "p1-16r-a":
        run_remote_script("tools/p1_16r_a_acquisition_remote.py", "p1_16r_a_acquisition_remote.py", timeout=2400)
    elif action == "download-p1-16r-a":
        download_reports(
            REMOTE_REPORT_REL_P1_16R_A,
            LOCAL_REPORT_DIR_P1_16R_A,
            [
                "p1_16r_ebola_source_acquisition.zip",
                "p1_16r_ebola_source_acquisition.zip.sha256",
            ],
        )
    elif action == "p1-17a":
        run_remote_script("tools/p1_17a_remote.py", "p1_17a_remote.py", timeout=2400)
    elif action == "download-p1-17a":
        download_reports(
            "reports/p1_17a_flunet_prediction_mvp",
            ROOT / "reports" / "p1_17a_flunet_prediction_mvp",
            [
                "p1_17a_flunet_prediction_mvp.zip",
                "p1_17a_flunet_prediction_mvp.zip.sha256",
            ],
        )
    elif action == "p1-17a-r":
        run_remote_script("tools/p1_17a_repair_remote.py", "p1_17a_repair_remote.py", timeout=3600)
    elif action == "p1-17a-r-inspect":
        run_remote_script("tools/p1_17a_r_inspect_remote.py", "p1_17a_r_inspect_remote.py", timeout=300)
    elif action == "p1-17a-auth-probe":
        run_remote_script("tools/p1_17a_auth_probe_remote.py", "p1_17a_auth_probe_remote.py", timeout=300)
    elif action == "p1-17a-browser-probe":
        run_remote_script("tools/p1_17a_browser_probe_remote.py", "p1_17a_browser_probe_remote.py", timeout=300)
    elif action == "stop-project":
        run_remote_script("tools/p1_stop_project_remote.py", "p1_stop_project_remote.py", timeout=300)
    elif action == "resource-probe":
        run_remote_script("tools/p1_remote_resource_probe.py", "p1_remote_resource_probe.py", timeout=300)
    elif action == "p1-18-preflight":
        run_remote_script("tools/p1_18_preflight_remote.py", "p1_18_preflight_remote.py", timeout=300)
    elif action == "p1-18":
        run_remote_script("tools/p1_18_deep_forecast_remote.py", "p1_18_deep_forecast_remote.py", timeout=900)
    elif action == "p1-18-r0":
        run_remote_script("tools/p1_18_r0_recovery_remote.py", "p1_18_r0_recovery_remote.py", timeout=7200)
    elif action == "p1-18-r0a":
        run_remote_script_no_secrets("tools/p1_18_r0a_identity_audit_remote.py", "p1_18_r0a_identity_audit_remote.py", timeout=1800)
    elif action == "p1-18-r0a1":
        run_remote_script_no_secrets("tools/p1_18_r0a1_contract_preflight_remote.py", "p1_18_r0a1_contract_preflight_remote.py", timeout=1800)
    elif action == "fasttrack-probe":
        run_remote_script_no_secrets("tools/fasttrack_probe_remote.py", "fasttrack_probe_remote.py", timeout=300)
    elif action == "fasttrack-db-summary":
        run_remote_script("tools/fasttrack_db_summary_remote.py", "fasttrack_db_summary_remote.py", timeout=300)
    elif action == "fasttrack-http-probe":
        run_remote_script_no_secrets("tools/fasttrack_http_probe_remote.py", "fasttrack_http_probe_remote.py", timeout=300)
    elif action == "fasttrack-browser-console-probe":
        run_remote_script_no_secrets("tools/fasttrack_browser_console_remote.py", "fasttrack_browser_console_remote.py", timeout=300)
    elif action == "fasttrack-constraint-probe":
        run_remote_script("tools/fasttrack_constraint_probe_remote.py", "fasttrack_constraint_probe_remote.py", timeout=300)
    elif action == "fasttrack-artifact-probe":
        run_remote_script_no_secrets("tools/fasttrack_artifact_probe_remote.py", "fasttrack_artifact_probe_remote.py", timeout=300)
    elif action == "fasttrack-e2e":
        run_remote_script("tools/fasttrack_e2e_remote.py", "fasttrack_e2e_remote.py", timeout=10800)
    elif action == "download-fasttrack-e2e":
        download_latest_fasttrack()
    elif action == "download-p1-18":
        download_latest_p1_18()
    elif action == "download-p1-18-r0":
        download_latest_p1_18_r0()
    elif action == "download-p1-18-r0a":
        download_latest_p1_18_r0a()
    elif action == "download-p1-18-r0a1":
        download_latest_p1_18_r0a1()
    elif action == "download-p1-17a-r":
        download_reports(
            REMOTE_REPORT_REL_P1_17A_R,
            LOCAL_REPORT_DIR_P1_17A_R,
            [],
        )
    elif action == "upload-p1-15b-local-updates":
        upload_p1_15b_local_updates()
    elif action == "upload-p1-15b-r2-local-updates":
        upload_p1_15b_r2_local_updates()
    else:
        raise SystemExit(f"unknown action: {action}")


if __name__ == "__main__":
    main()
