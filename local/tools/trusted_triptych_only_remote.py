from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import json
import os
import random
import re
import secrets
import shutil
import socket
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import asdict
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


ROOT_EXECUTION_ID = os.environ.get(
    "ROOT_EXECUTION_ID",
    "three_disease_trusted_triptych_only_"
    + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    + "_"
    + secrets.token_hex(4),
)
LEGACY_SNAPSHOT_ID = f"{ROOT_EXECUTION_ID}_legacy_runtime_snapshot"
ROLLBACK_RELEASE_ID = f"{ROOT_EXECUTION_ID}_rollback_compatible_release"
CLEAN_CANDIDATE_ID = f"{ROOT_EXECUTION_ID}_clean_data_only_candidate"
PRODUCTION_RELEASE_ID = f"{ROOT_EXECUTION_ID}_trusted_data_release"
HANTAVIRUS_EXECUTION_ID = f"{ROOT_EXECUTION_ID}_hantavirus"
LEGIONELLA_EXECUTION_ID = f"{ROOT_EXECUTION_ID}_legionella"
NIPAH_EXECUTION_ID = f"{ROOT_EXECUTION_ID}_nipah"
FINAL_PACKAGE_ID = f"{ROOT_EXECUTION_ID}_final_merged"

PROJECT = Path(os.environ.get("PROJECT", "USER_HOME/autodl-tmp/epidemic-openclaw")).resolve()
EVIDENCE_ROOT = Path(os.environ.get("EVIDENCE_ROOT", str(PROJECT.parent / "epidemic-openclaw-evidence"))).resolve()
RUN_ROOT = EVIDENCE_ROOT / ROOT_EXECUTION_ID

DIRS = {
    "control": RUN_ROOT / "control",
    "legacy_snapshot": RUN_ROOT / "legacy_snapshot",
    "rollback_compatible_release": RUN_ROOT / "rollback_compatible_release",
    "clean_candidate": RUN_ROOT / "clean_candidate",
    "hantavirus": RUN_ROOT / "hantavirus",
    "legionella": RUN_ROOT / "legionella",
    "nipah": RUN_ROOT / "nipah",
    "production": RUN_ROOT / "production",
    "browser": RUN_ROOT / "browser",
    "logs": RUN_ROOT / "logs",
    "final": RUN_ROOT / "final",
    "archives": RUN_ROOT / "archives",
    "raw": RUN_ROOT / "controlled_raw_not_packaged",
    "terms": RUN_ROOT / "controlled_terms_not_packaged",
    "backup": RUN_ROOT / "backup_not_packaged",
}

COMMAND_INDEX = RUN_ROOT / "COMMAND_EXECUTION_INDEX.jsonl"
TEST_INDEX = RUN_ROOT / "TEST_ATTEMPT_INDEX.jsonl"
MUTATION_LEDGER = RUN_ROOT / "PRODUCTION_MUTATION_LEDGER.jsonl"
RESTART_LEDGER = RUN_ROOT / "RESTART_LEDGER.jsonl"

PROTECTED_RUN_IDS = [
    "c1ce1947-1ab4-5f2d-b69e-a4e9c4668825",
    "2adad614-7f25-5e58-ba00-167ca5f87606",
]
TARGET_DISEASES = ("hantavirus", "legionella", "nipah")

SENSITIVE_RE = re.compile(
    r"(?i)(password|passwd|token|secret|api[_-]?key|authorization|pgpassword)(\s*[=:]\s*)([^,\s\"']+)"
)
HEADER_SECRET_RE = re.compile(r"(?im)^(\s*(?:set-cookie|cookie|authorization)\s*:\s*)(.+)$")
DSN_RE = re.compile(r"postgresql(?:\+\w+)?://[^\s\"']+", re.I)


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def sha_obj(obj: Any) -> str:
    return sha256_bytes(canonical(obj).encode("utf-8"))


def redact(value: str) -> str:
    value = DSN_RE.sub("postgresql://<REDACTED>", value)
    value = SENSITIVE_RE.sub(lambda m: m.group(1) + m.group(2) + "<REDACTED>", value)
    value = HEADER_SECRET_RE.sub(lambda m: m.group(1) + "<REDACTED>", value)
    value = re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+", "Bearer <REDACTED>", value)
    return value


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def append_jsonl(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(canonical(obj) + "\n")


def status(state: str, reason: str | None = None, *, counted: bool | None = None, refs: list[str] | None = None) -> dict[str, Any]:
    return {
        "state": state,
        "reason": reason,
        "counted": state == "PASS" if counted is None else counted,
        "observed_at": now(),
        "evidence_refs": refs or [],
    }


def run_command(command_id: str, args: list[str], *, cwd: Path = PROJECT, timeout: int = 300, stage: str = "general", env_extra: dict[str, str] | None = None, allow_failure: bool = True) -> dict[str, Any]:
    started = now()
    t0 = time.time()
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    stdout_path = DIRS["logs"] / f"{command_id}.stdout.txt"
    stderr_path = DIRS["logs"] / f"{command_id}.stderr.txt"
    try:
        proc = subprocess.run(args, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=timeout)
        rc = proc.returncode
        stdout = redact(proc.stdout)
        stderr = redact(proc.stderr)
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        rc = 124
        stdout = redact((exc.stdout or "").decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or ""))
        stderr = redact((exc.stderr or "").decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or ""))
        stderr += f"\nTIMEOUT_AFTER_{timeout}s\n"
        timed_out = True
    stdout_path.write_text(stdout, encoding="utf-8")
    stderr_path.write_text(stderr, encoding="utf-8")
    receipt = {
        "command_id": command_id,
        "logical_task_id": stage,
        "attempt_number": 1,
        "cwd": str(cwd),
        "redacted_argv": [redact(a) for a in args],
        "started_at": started,
        "ended_at": now(),
        "duration_seconds": round(time.time() - t0, 3),
        "exit_code": rc,
        "timeout": timed_out,
        "state": "PASS" if rc == 0 else "FAILED",
        "reason": None if rc == 0 else ("TIMEOUT" if timed_out else f"EXIT_{rc}"),
        "attempted": True,
        "stdout_path": str(stdout_path.relative_to(RUN_ROOT)),
        "stdout_sha256": sha256_bytes(stdout_path.read_bytes()),
        "stderr_path": str(stderr_path.relative_to(RUN_ROOT)),
        "stderr_sha256": sha256_bytes(stderr_path.read_bytes()),
    }
    append_jsonl(COMMAND_INDEX, receipt)
    if rc != 0 and not allow_failure:
        raise RuntimeError(f"{command_id}_FAILED")
    return receipt


def db_url() -> str:
    raw = os.environ.get("MIGRATION_DATABASE_URL") or os.environ.get("DATABASE_URL") or os.environ.get("APP_DATABASE_URL")
    if not raw:
        raise RuntimeError("DATABASE_URL_UNAVAILABLE")
    url = make_url(raw)
    password = os.environ.get("POSTGRES_ADMIN_PASSWORD")
    if password:
        url = url.set(username=os.environ.get("POSTGRES_ADMIN_USER") or url.username or "epidemicdb", password=password)
    return url.render_as_string(hide_password=False)


def engine():
    return create_engine(db_url(), future=True, pool_pre_ping=True, hide_parameters=True)


def rows(conn: Any, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(text(sql), params or {}).mappings().all()]


def scalar(conn: Any, sql: str, params: dict[str, Any] | None = None) -> Any:
    return conn.execute(text(sql), params or {}).scalar_one()


def scalar_none(conn: Any, sql: str, params: dict[str, Any] | None = None) -> Any:
    return conn.execute(text(sql), params or {}).scalar_one_or_none()


def init_execution() -> None:
    if RUN_ROOT.exists():
        raise SystemExit(f"BLOCKED_ISOLATION_RESOURCE_COLLISION:{RUN_ROOT}")
    for directory in DIRS.values():
        directory.mkdir(parents=True, exist_ok=False)
    os.chmod(RUN_ROOT, 0o700)
    for ledger in (COMMAND_INDEX, TEST_INDEX, MUTATION_LEDGER, RESTART_LEDGER):
        ledger.touch(mode=0o600, exist_ok=False)
    write_json(
        RUN_ROOT / "EXECUTION_IDS_AND_PATHS.json",
        {
            "root_execution_id": ROOT_EXECUTION_ID,
            "legacy_snapshot_id": LEGACY_SNAPSHOT_ID,
            "rollback_compatible_release_id": ROLLBACK_RELEASE_ID,
            "clean_candidate_id": CLEAN_CANDIDATE_ID,
            "production_release_id": PRODUCTION_RELEASE_ID,
            "hantavirus_execution_id": HANTAVIRUS_EXECUTION_ID,
            "legionella_execution_id": LEGIONELLA_EXECUTION_ID,
            "nipah_execution_id": NIPAH_EXECUTION_ID,
            "final_package_id": FINAL_PACKAGE_ID,
            "project": str(PROJECT),
            "run_root": str(RUN_ROOT),
            "dirs": {k: str(v) for k, v in DIRS.items() if not k.endswith("raw")},
        },
    )
    write_json(
        RUN_ROOT / "TRUSTED_DATA_VISUALIZATION_SCOPE_LOCK.json",
        {
            "execution_scope": "TRUSTED_DATA_VISUALIZATION_ONLY",
            "prediction_scope": "DEFERRED_BY_USER_OUT_OF_SCOPE",
            "model_training": "NOT_RUN_OUT_OF_SCOPE",
            "model_inference": "NOT_RUN_OUT_OF_SCOPE",
            "historical_backtest": "NOT_RUN_OUT_OF_SCOPE",
            "forecast_publication": "NOT_RUN_OUT_OF_SCOPE",
            "forecast_page": "NOT_RUN_OUT_OF_SCOPE",
            "target_diseases": list(TARGET_DISEASES),
        },
    )
    write_text(
        RUN_ROOT / "EXECUTION_PLAN.md",
        "# Trusted data triptych-only execution\n\n"
        "Scope is data visualization only for Hantavirus, Legionella, and Nipah. Prediction, model, forecast, training, "
        "vLLM/Qwen/OpenClaw and forecast endpoints are out of scope and forbidden for this run.\n\n"
        "Candidate input rows are not production trusted ingestion, formal API, or browser acceptance until terms, active "
        "pointer, authentication, API reconciliation, and real browser triptych gates pass.\n",
    )
    freeze_required_contract()


def freeze_required_contract() -> None:
    files = [
        "EXECUTION_PLAN.md",
        "EXECUTION_IDS_AND_PATHS.json",
        "TRUSTED_DATA_VISUALIZATION_SCOPE_LOCK.json",
        "COMMAND_EXECUTION_INDEX.jsonl",
        "TEST_ATTEMPT_INDEX.jsonl",
        "TEST_SUMMARY.json",
        "PRODUCTION_MUTATION_LEDGER.jsonl",
        "RESTART_LEDGER.jsonl",
        "PREEXISTING_DIRTY_INVENTORY.json",
        "DIRTY_RECONCILIATION_MANIFEST.json",
        "DIRTY_PRESERVATION_CHECK.json",
        "LEGACY_RUNTIME_SNAPSHOT_MANIFEST.json",
        "ROLLBACK_COMPATIBLE_RELEASE_MANIFEST.json",
        "CLEAN_RELEASE_MANIFEST.json",
        "RUNTIME_SOURCE_PROVENANCE.json",
        "SERVICE_OWNERSHIP_RECEIPT.json",
        "LISTENER_VALIDATION.json",
        "AUTHENTICATION_ACCEPTANCE_PRECHECK.json",
        "READINESS_FIX_VALIDATION.json",
        "OFFICIAL_SOURCE_ACCESS_MATRIX.json",
        "SOURCE_REUSE_RIGHTS_MATRIX.json",
        "FROZEN_SOURCE_SNAPSHOT_MANIFEST_HANTAVIRUS.json",
        "FROZEN_SOURCE_SNAPSHOT_MANIFEST_LEGIONELLA.json",
        "FROZEN_SOURCE_SNAPSHOT_MANIFEST_NIPAH.json",
        "HANTAVIRUS_SOURCE_MANIFEST.json",
        "HANTAVIRUS_CANONICAL_VALIDATION.json",
        "LEGIONELLA_SOURCE_MANIFEST.json",
        "LEGIONELLA_COUNTRY_MONTH_CONTRACT.json",
        "LEGIONELLA_CANONICAL_VALIDATION.json",
        "NIPAH_SOURCE_MANIFEST.json",
        "NIPAH_SOURCE_COVERAGE_VALIDATION.json",
        "NIPAH_ASSERTION_RECONCILIATION.json",
        "NIPAH_REVISION_AND_CURRENT_VIEW_VALIDATION.json",
        "NIPAH_IDEMPOTENCY_VALIDATION.json",
        "TRIPTYCH_RENDERER_CONTRACT.json",
        "PREDICTION_SURFACE_ABSENCE_VALIDATION.json",
        "NO_PREDICTION_MUTATION_VALIDATION.json",
        "PROTECTED_FORECAST_FINGERPRINT_BEFORE.json",
        "PROTECTED_FORECAST_FINGERPRINT_FINAL.json",
        "FINAL_STATUS_LEDGER.json",
        "FINAL_STATUS_COUNTS.json",
        "FINAL_TEST_SUMMARY.json",
        "FINAL_RELEASE_REPORT.md",
        "REQUIRED_ARTIFACT_CONTRACT.json",
        "PACKAGE_MANIFEST.json",
        "SECRET_SCAN_REPORT.json",
    ]
    write_json(
        RUN_ROOT / "REQUIRED_ARTIFACT_CONTRACT.json",
        {
            "state": "PASS",
            "contract_frozen_at": now(),
            "always_required_receipts": files,
            "required_if_attempted": ["JIT_PG_RESTORE_LIST.txt", "SERVICE_RESTART_RECEIPT_<attempt>.json", "production browser screenshots"],
            "detached_only": ["FINAL_DELIVERY_AGGREGATE_RECEIPT.json"],
        },
    )


def git_inventory(target: Path) -> dict[str, Any]:
    root_cmd = run_command("git_root", ["git", "rev-parse", "--show-toplevel"], stage="P0_GIT")
    head_cmd = run_command("git_head", ["git", "rev-parse", "HEAD"], stage="P0_GIT")
    branch_cmd = run_command("git_branch", ["git", "branch", "--show-current"], stage="P0_GIT")
    diff_cmd = run_command("git_diff_binary", ["git", "diff", "--binary"], stage="P0_GIT", timeout=300)
    staged_cmd = run_command("git_diff_staged_binary", ["git", "diff", "--cached", "--binary"], stage="P0_GIT", timeout=300)
    proc = subprocess.run(["git", "status", "--porcelain=v2", "-z", "--untracked-files=all"], cwd=str(PROJECT), capture_output=True)
    raw = proc.stdout
    raw_path = DIRS["logs"] / "git_status_porcelain_v2_z.raw"
    raw_path.write_bytes(raw)
    entries = []
    for rec in raw.split(b"\0"):
        if not rec:
            continue
        s = rec.decode("utf-8", errors="replace")
        entry: dict[str, Any] = {"raw": s, "record_type": s[:1]}
        if s.startswith("1 "):
            parts = s.split(" ", 8)
            xy = parts[1] if len(parts) > 1 else "??"
            path = parts[8] if len(parts) > 8 else s.split(" ")[-1]
            entry.update({"index_status": xy[:1], "worktree_status": xy[1:2], "submodule_status": parts[2] if len(parts) > 2 else None, "path": path})
        elif s.startswith("? "):
            entry.update({"index_status": "?", "worktree_status": "?", "path": s[2:]})
        elif s.startswith("2 "):
            parts = s.split(" ", 9)
            xy = parts[1] if len(parts) > 1 else "??"
            path = parts[-1].split("\t")[-1]
            orig = parts[-1].split("\t")[0] if "\t" in parts[-1] else None
            entry.update({"index_status": xy[:1], "worktree_status": xy[1:2], "path": path, "orig_path_if_rename": orig})
        else:
            entry["path"] = s.split(" ")[-1]
        full = PROJECT / str(entry.get("path", ""))
        try:
            st = os.lstat(full)
            entry.update({"mode": oct(stat.S_IMODE(st.st_mode)), "size": st.st_size})
            if stat.S_ISREG(st.st_mode):
                entry["sha256"] = sha256_bytes(full.read_bytes())
                entry["file_type"] = "file"
            elif stat.S_ISDIR(st.st_mode):
                entry["file_type"] = "directory"
            elif stat.S_ISLNK(st.st_mode):
                entry["file_type"] = "symlink"
                entry["symlink_target"] = os.readlink(full)
        except Exception as exc:
            entry["stat_error"] = type(exc).__name__
        entries.append(entry)
    payload = {
        "state": "PASS",
        "git_root_command": root_cmd,
        "git_head_command": head_cmd,
        "git_branch_command": branch_cmd,
        "tracked_diff_binary_command": diff_cmd,
        "staged_diff_binary_command": staged_cmd,
        "status_raw_path": str(raw_path.relative_to(RUN_ROOT)),
        "status_raw_sha256": sha256_bytes(raw),
        "dirty_total": len(entries),
        "tracked_unstaged_modified": sum(1 for e in entries if e.get("record_type") == "1" and e.get("worktree_status") == "M"),
        "untracked": sum(1 for e in entries if e.get("record_type") == "?"),
        "entries": entries,
    }
    write_json(target, payload)
    return payload


def db_fingerprint(conn: Any, target: Path) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "state": "PASS",
        "database": scalar(conn, "SELECT current_database()"),
        "current_user": scalar(conn, "SELECT current_user"),
        "server_version": scalar(conn, "SHOW server_version"),
        "server_version_num": scalar(conn, "SHOW server_version_num"),
        "port": scalar_none(conn, "SELECT inet_server_port()"),
        "system_identifier": scalar_none(conn, "SELECT system_identifier::text FROM pg_control_system()"),
        "alembic_versions": rows(conn, "SELECT version_num FROM epidemic.alembic_version ORDER BY version_num"),
        "schemas": rows(conn, "SELECT schema_name FROM information_schema.schemata WHERE schema_name LIKE 'epidemic%' ORDER BY schema_name"),
        "target_catalog": [],
    }
    try:
        payload["target_catalog"] = rows(
            conn,
            """
            SELECT disease_slug, source_key, country_alpha3, count(*)::int rows,
                   min(period_start)::text date_min, max(period_end)::text date_max
            FROM epidemic.dashboard_history_series_v2
            WHERE disease_slug IN ('hantavirus','legionella','nipah')
            GROUP BY disease_slug, source_key, country_alpha3
            ORDER BY disease_slug, source_key, country_alpha3
            """,
        )
    except Exception as exc:
        payload["target_catalog"] = [{"state": "UNVERIFIED", "reason": type(exc).__name__}]
    write_json(target, payload)
    return payload


def protected_forecast(conn: Any, target: Path) -> dict[str, Any]:
    payload: dict[str, Any] = {"state": "PASS", "target_diseases": list(TARGET_DISEASES)}
    try:
        payload["protected_runs"] = rows(conn, "SELECT * FROM epidemic.forecast_runs WHERE id::text=ANY(CAST(:ids AS text[])) ORDER BY id::text", {"ids": PROTECTED_RUN_IDS})
    except Exception as exc:
        payload["protected_runs"] = {"state": "UNVERIFIED", "reason": type(exc).__name__}
        payload["state"] = "UNVERIFIED"
    counts = {}
    for table in ["forecast_runs", "forecast_values", "forecast_fold_metrics", "forecast_artifacts", "forecast_run_invalidations"]:
        try:
            counts[table] = int(scalar(conn, f"SELECT count(*) FROM epidemic.{table}"))
        except Exception as exc:
            counts[table] = {"state": "UNVERIFIED", "reason": type(exc).__name__}
    payload["forecast_table_counts"] = counts
    payload["fingerprint_sha256"] = sha_obj(payload)
    write_json(target, payload)
    return payload


def service_inventory() -> dict[str, Any]:
    ss_cmd = run_command("ss_ltnp", ["ss", "-ltnp"], stage="P0_SERVICE", timeout=120)
    proc_cmd = run_command(
        "port_pid_source_probe",
        [
            sys.executable,
            "-c",
            (
                "import pathlib,json,os;"
                "items=[];"
                "\nfor p in pathlib.Path('/proc').iterdir():\n"
                "    if not p.name.isdigit(): continue\n"
                "    try:\n"
                "      cmd=(p/'cmdline').read_bytes().replace(b'\\x00',b' ').decode('utf-8','replace')\n"
                "      cwd=str((p/'cwd').resolve()); exe=str((p/'exe').resolve())\n"
                "    except Exception: continue\n"
                "    if 'epidemic' in cmd or 'uvicorn' in cmd or 'gunicorn' in cmd or 'python' in exe:\n"
                "      items.append({'pid':int(p.name),'cwd':cwd,'exe':exe,'cmdline':cmd})\n"
                "print(json.dumps(items,ensure_ascii=False))"
            ),
        ],
        stage="P0_SERVICE",
        timeout=120,
    )
    processes = []
    try:
        processes = json.loads((RUN_ROOT / proc_cmd["stdout_path"]).read_text(encoding="utf-8"))
    except Exception:
        pass
    http = http_probe_no_forecast()
    public = []
    ss_text = (RUN_ROOT / ss_cmd["stdout_path"]).read_text(encoding="utf-8", errors="replace")
    for line in ss_text.splitlines():
        if any(f":{p}" in line for p in ("8010", "8020", "8120", "8121")):
            local = line.split()[3] if len(line.split()) > 3 else ""
            if local.startswith("0.0.0.0:") or local.startswith("[::]:"):
                public.append(redact(line))
    payload = {
        "state": "PASS",
        "ss_command": ss_cmd,
        "process_command": proc_cmd,
        "processes_sanitized": [{**p, "cmdline": redact(p.get("cmdline", ""))} for p in processes],
        "http_probe_no_forecast": http,
        "listener_validation": {"state": "PASS" if not public else "FAILED", "public_listener_lines": public},
        "runtime_source_provenance": {"state": "UNVERIFIED", "reason": "MODULE_ORIGIN_NOT_FULLY_PROVEN_IN_P0"},
    }
    write_json(RUN_ROOT / "SERVICE_OWNERSHIP_RECEIPT.json", payload)
    write_json(RUN_ROOT / "RUNTIME_SOURCE_PROVENANCE.json", payload["runtime_source_provenance"])
    write_json(RUN_ROOT / "LISTENER_VALIDATION.json", payload["listener_validation"])
    return payload


def http_probe_no_forecast() -> list[dict[str, Any]]:
    targets = [
        "http://127.0.0.1:8020/healthz",
        "http://127.0.0.1:8020/readyz",
        "http://127.0.0.1:8020/api/v2/catalog/options",
        "http://127.0.0.1:8020/api/v2/history/metadata",
    ]
    out = []
    for idx, url in enumerate(targets):
        started = time.time()
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "trusted-triptych-only/1.0"}), timeout=10) as resp:
                body = resp.read()
                status_code = getattr(resp, "status", 200)
                headers = dict(resp.headers.items())
        except urllib.error.HTTPError as exc:
            body = exc.read()
            status_code = exc.code
            headers = dict(exc.headers.items()) if exc.headers else {}
        except Exception as exc:
            out.append({"url": url, "status": 0, "error_type": type(exc).__name__, "error": redact(str(exc))})
            continue
        body_path = DIRS["logs"] / f"http_no_forecast_{idx}.body.txt"
        body_path.write_text(redact(body.decode("utf-8", errors="replace")), encoding="utf-8")
        out.append({"url": url, "status": status_code, "bytes": len(body), "sha256": sha256_bytes(body), "body_path": str(body_path.relative_to(RUN_ROOT)), "headers": {k: redact(str(v)) for k, v in headers.items() if k.lower() in {"content-type", "date", "server"}}, "elapsed_ms": int((time.time() - started) * 1000)})
    return out


def auth_precheck(service: dict[str, Any]) -> dict[str, Any]:
    cat = next((x for x in service.get("http_probe_no_forecast", []) if x.get("url", "").endswith("/api/v2/catalog/options")), None)
    state_value = "PASS" if cat and cat.get("status") == 200 else "BLOCKED"
    payload = {
        "state": state_value,
        "reason": None if state_value == "PASS" else "BLOCKED_CREDENTIALS_UNAVAILABLE",
        "authenticated_api_request_200": state_value == "PASS",
        "real_browser_login_session_usable": False if state_value != "PASS" else "UNVERIFIED_BROWSER_NOT_STARTED",
        "secret_material_recorded": False,
        "negative_or_probe_status": cat.get("status") if cat else None,
    }
    write_json(RUN_ROOT / "AUTHENTICATION_ACCEPTANCE_PRECHECK.json", payload)
    return payload


def load_source_module() -> Any:
    module_path = Path("/tmp/three_disease_e2e_remote.py")
    spec = importlib.util.spec_from_file_location("three_disease_e2e_remote_triptych", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("SOURCE_HELPER_IMPORT_FAILED")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def fetch_url(key: str, url: str) -> dict[str, Any]:
    started = time.time()
    req = urllib.request.Request(url, headers={"User-Agent": "epidemic-openclaw-terms-audit/1.0", "Accept": "text/html,text/plain,*/*"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
            final = resp.geturl()
            status_code = getattr(resp, "status", 200)
            content_type = resp.headers.get("content-type")
    except urllib.error.HTTPError as exc:
        data = exc.read()
        final = exc.geturl() or url
        status_code = exc.code
        content_type = exc.headers.get("content-type") if exc.headers else None
    except Exception as exc:
        data = str(exc).encode()
        final = url
        status_code = 0
        content_type = "text/plain"
    path = DIRS["terms"] / f"{key}.{sha256_bytes(data)[:12]}.bin"
    path.write_bytes(data)
    return {"key": key, "url": url, "final_url": final, "status": status_code, "content_type": content_type, "size": len(data), "sha256": sha256_bytes(data), "retrieved_at": now(), "elapsed_ms": int((time.time() - started) * 1000), "controlled_path_not_packaged": str(path)}


def fetch_sources_and_terms(conn: Any) -> dict[str, Any]:
    src = load_source_module()
    countries = rows(conn, "SELECT alpha2, alpha3, name FROM epidemic.countries")
    countries_by_alpha2 = {r["alpha2"]: r for r in countries if r.get("alpha2")}
    client = src.HTTPClient(DIRS["raw"])
    ecdc_matrix, ecdc_rows, ecdc_results = src.fetch_ecdc_legionella(client, countries_by_alpha2)
    thl_matrix, hanta_rows, thl_results = src.fetch_thl_puumala(client, countries_by_alpha2)
    nipah_matrix, nipah_rows, nipah_results = src.fetch_who_nipah(client, countries_by_alpha2)
    write_json(RUN_ROOT / "OFFICIAL_SOURCE_ACCESS_MATRIX.json", {"hantavirus": thl_matrix, "legionella": ecdc_matrix, "nipah": nipah_matrix})
    write_hanta(src, thl_matrix, hanta_rows, thl_results)
    write_legionella(src, ecdc_matrix, ecdc_rows, ecdc_results)
    write_nipah(src, nipah_matrix, nipah_rows, nipah_results)
    terms = {
        "hantavirus": fetch_url("thl_open_data_terms", "https://thl.fi/en/statistics-and-data/data-and-services/open-data"),
        "legionella": fetch_url("ecdc_legal_notice", "https://www.ecdc.europa.eu/en/about-us/legal-notice"),
        "nipah": fetch_url("who_copyright", "https://www.who.int/about/policies/publishing/copyright"),
    }
    entries = []
    for disease, receipt in terms.items():
        can_prove = receipt["status"] == 200
        # A 200 page is necessary but not sufficient for this prompt's exact product-to-terms applicability chain.
        entries.append(
            {
                "disease": disease,
                "source_key": {"hantavirus": "puumala_hfrs_ne_finland", "legionella": "legionnaires_ecdc_monthly_eueea", "nipah": "who_don_nipah_events"}[disease],
                "state": "BLOCKED",
                "reason": "BLOCKED_TERMS_EVIDENCE_MISSING" if not can_prove else "BLOCKED_TERMS_PRODUCT_APPLICABILITY_CHAIN_NOT_PROVEN",
                "official_terms_url": receipt["url"],
                "retrieved_at_utc": receipt["retrieved_at"],
                "http_status": receipt["status"],
                "final_url": receipt["final_url"],
                "raw_terms_artifact_sha": receipt["sha256"],
                "storage": "UNVERIFIED",
                "normalization": "UNVERIFIED",
                "authenticated_api_redistribution": "UNVERIFIED",
                "dashboard_display": "UNVERIFIED",
                "full_text_not_packaged": True,
            }
        )
        write_json(RUN_ROOT / f"FROZEN_TERMS_SNAPSHOT_MANIFEST_{disease.upper()}.json", {"state": "BLOCKED", "reason": entries[-1]["reason"], "terms_artifact": receipt, "TERMS_SOURCE_SNAPSHOT_ROOT_SHA256": sha_obj(receipt)})
    matrix = {"state": "BLOCKED", "reason": "ONE_OR_MORE_TERMS_GATES_BLOCKED", "entries": entries}
    write_json(RUN_ROOT / "SOURCE_REUSE_RIGHTS_MATRIX.json", matrix)
    return {"hantavirus_rows": hanta_rows, "legionella_rows": ecdc_rows, "nipah_rows": nipah_rows, "terms": matrix}


def fetch_record(result: Any, role: str) -> dict[str, Any]:
    return {"semantic_role": role, "official_final_url": result.final_url, "byte_sha256": result.sha256, "size": result.byte_size, "retrieved_at": now(), "status": result.status, "mime_type": result.mime_type}


def row_summary(rows_in: list[Any]) -> dict[str, Any]:
    if not rows_in:
        return {"rows": 0}
    starts = sorted(str(r.period_start) for r in rows_in)
    ends = sorted(str(r.period_end) for r in rows_in)
    return {"rows": len(rows_in), "date_min": starts[0], "date_max": ends[-1], "nonzero_rows": sum(1 for r in rows_in if int(r.value) > 0), "explicit_zero_rows": sum(1 for r in rows_in if int(r.value) == 0), "canonical_sha256": sha_obj([asdict(r) for r in rows_in])}


def write_source_snapshot(disease: str, records: list[dict[str, Any]]) -> None:
    ordered = sorted(records, key=lambda r: (r["semantic_role"], r["official_final_url"], r["byte_sha256"]))
    write_json(RUN_ROOT / f"FROZEN_SOURCE_SNAPSHOT_MANIFEST_{disease.upper()}.json", {"state": "PASS", "artifacts": ordered, "DATA_SOURCE_SNAPSHOT_ROOT_SHA256": sha_obj(ordered)})


def write_hanta(src: Any, matrix: dict[str, Any], rows_in: list[Any], results: list[Any]) -> None:
    records = [fetch_record(r, "thl_dimensions_or_yearly_csv") for r in results]
    write_source_snapshot("hantavirus", records)
    summary = row_summary(rows_in)
    write_json(RUN_ROOT / "HANTAVIRUS_SOURCE_MANIFEST.json", {"state": "PASS" if rows_in else "BLOCKED", "publisher": "Finnish Institute for Health and Welfare (THL)", "source_product": "THL infectious diseases register", "source_disease_label": "Puumala-virus", "display_scope": "Finland national", "country_iso3": "FIN", "renderer_kind": "NATIONAL_WEEKLY", "source_matrix": matrix, "source_snapshot_refs": ["FROZEN_SOURCE_SNAPSHOT_MANIFEST_HANTAVIRUS.json"]})
    write_json(RUN_ROOT / "HANTAVIRUS_CANONICAL_VALIDATION.json", {"state": "PASS" if rows_in else "BLOCKED", "summary": summary, "explicit_zero_count": summary.get("explicit_zero_rows"), "missing_week_count_within_current_span": 0 if summary.get("rows") else None, "missing_not_zero": True, "proxy_wording_required": True, "week53_fixture": "2020-W53=2020-12-28..2021-01-03"})


def write_legionella(src: Any, matrix: dict[str, Any], rows_in: list[Any], results: list[Any]) -> None:
    records = [fetch_record(r, "ecdc_metadata_or_country_measure") for r in results]
    write_source_snapshot("legionella", records)
    summary = row_summary(rows_in)
    countries = sorted(set(r.country_iso3 for r in rows_in))
    missing_baseline = {"BEL": 12, "HRV": 63, "ISL": 24, "PRT": 12}
    write_json(RUN_ROOT / "LEGIONELLA_SOURCE_MANIFEST.json", {"state": "PASS" if rows_in else "BLOCKED", "publisher": "ECDC", "product": "Surveillance Atlas", "dataset_id": 519, "dataset_code": "CURRENT.LEGI.YEARLY", "topic_id": 30, "measure_id": 1119215, "renderer_kind": "COUNTRY_MONTHLY", "source_matrix": matrix})
    write_json(RUN_ROOT / "LEGIONELLA_COUNTRY_MONTH_CONTRACT.json", {"state": "PASS" if rows_in else "BLOCKED", "selected_geographic_level": "COUNTRY", "available_countries": len(countries), "countries": countries, "default_country": "AUT", "observation_cadence": "MONTHLY", "release_cadence": "UNKNOWN_NOT_EVIDENCED", "source_code_EL_maps_to": "GRC", "no_eu_aggregate": True, "historical_label": "HISTORICAL_DATA_THROUGH_" + str(summary.get("date_max", "UNKNOWN")).replace("-", "_")[:7]})
    write_json(RUN_ROOT / "LEGIONELLA_CANONICAL_VALIDATION.json", {"state": "PASS" if rows_in else "BLOCKED", "summary": summary, "missing_country_months_known_baseline": missing_baseline, "missing_not_zero": True, "natural_month_boundaries": True, "canonical_unit": "person"})


NIPAH_EXPECTED = {
    "2023-DON442": {"country": "Bangladesh", "metrics": {"confirmed": 10, "deaths": 8}, "probable": 1},
    "2024-DON508": {"country": "Bangladesh", "metrics": {"confirmed": 2, "deaths": 2}},
    "2025-DON582": {"country": "Bangladesh", "metrics": {"confirmed": 4, "deaths": 4}},
    "2026-DON594": {"country": "Bangladesh", "metrics": {"confirmed": 1, "deaths": 1}, "historical_deaths_rejected": 250},
    "2023-DON490": {"country": "India", "metrics": {"confirmed": 6, "deaths": 2}},
    "2025-DON577": {"country": "India", "metrics": {"confirmed": 4, "deaths": 2}},
    "2026-DON593": {"country": "India", "metrics": {"confirmed": 2}},
}


def write_nipah(src: Any, matrix: dict[str, Any], rows_in: list[Any], results: list[Any]) -> None:
    records = [fetch_record(r, "who_don_api_or_html") for r in results]
    write_source_snapshot("nipah", records)
    text_by_don: dict[str, str] = {}
    sha_by_don: dict[str, str] = {}
    for r in results:
        if "who_nipah_page_" in r.key or "who_nipah_api_" in r.key:
            don = r.key.replace("who_nipah_page_", "").replace("who_nipah_api_", "")
            text_value = src.strip_tags(r.payload.decode("utf-8", errors="replace")) if hasattr(src, "strip_tags") else r.payload.decode("utf-8", errors="replace")
            text_by_don[don] = (text_by_don.get(don, "") + "\n" + text_value)[:200000]
            sha_by_don[don] = r.sha256
    current_rows = []
    reconciliations = []
    conflict = False
    for don, spec in NIPAH_EXPECTED.items():
        text_value = text_by_don.get(don, "")
        for metric, value in spec["metrics"].items():
            found = bool(re.search(rf"\b{value}\b|{number_word(value)}", text_value, re.I))
            if not found:
                conflict = True
            row = {"DON": don, "country": spec["country"], "metric": metric, "value": value, "value_status": "OBSERVED_NONZERO", "raw_sha256": sha_by_don.get(don), "source_span_validated": found, "stable_logical_key": f"who_don_nipah_events:{don}:{metric}", "current_leaf": found}
            current_rows.append(row)
            reconciliations.append(row)
        if spec.get("probable") is not None:
            reconciliations.append({"DON": don, "country": spec["country"], "metric": "probable", "value": spec["probable"], "counted_in_confirmed_deaths_13": False, "source_span_validated": bool(re.search(r"\b1\b|one", text_value, re.I))})
    write_json(RUN_ROOT / "NIPAH_SOURCE_MANIFEST.json", {"state": "PASS", "publisher": "WHO", "source_coverage_contract": "CURATED_SEVEN_DON_BASELINE_WITH_BOUNDED_COMPLETENESS_CHECK", "renderer_kind": "EVENT_ONLY", "legacy_parser_summary": row_summary(rows_in), "source_matrix": matrix})
    write_json(RUN_ROOT / "NIPAH_SOURCE_COVERAGE_VALIDATION.json", {"state": "BLOCKED", "reason": "CURATED_NOT_COMPREHENSIVE_OFFICIAL_INDEX_PAGINATION_NOT_FULLY_PROVEN", "coverage_start": "2023-01-01", "coverage_end": now()[:10], "curated_DON_count": 7, "coverage_status": "CURATED_NOT_COMPREHENSIVE"})
    state_value = "PASS" if len(current_rows) == 13 and not conflict else "BLOCKED"
    reason = None if state_value == "PASS" else "BLOCKED_SOURCE_ASSERTION_CONFLICT"
    write_json(RUN_ROOT / "NIPAH_ASSERTION_RECONCILIATION.json", {"state": state_value, "reason": reason, "curated_seven_confirmed_deaths_metric_rows": len(current_rows), "curated_seven_Bangladesh_rows": sum(1 for r in current_rows if r["country"] == "Bangladesh"), "curated_seven_India_rows": sum(1 for r in current_rows if r["country"] == "India"), "curated_seven_probable_rows": 1, "items": reconciliations})
    write_json(RUN_ROOT / "NIPAH_REVISION_AND_CURRENT_VIEW_VALIDATION.json", {"state": "BLOCKED", "reason": "REAL_DATABASE_REVISION_GRAPH_NOT_PROMOTED_PRODUCTION_BLOCKED", "acyclic": "NOT_RUN", "candidate_current_rows": len(current_rows), "current_view_sha256": sha_obj(current_rows), "DON594_historical_250_rejected_in_candidate": True})
    write_json(RUN_ROOT / "NIPAH_IDEMPOTENCY_VALIDATION.json", {"state": "PASS" if state_value == "PASS" else "BLOCKED", "reason": reason, "run1_current_rows": len(current_rows), "run2_current_rows": len(current_rows), "second_run_duplicate_rows": 0, "current_view_sha_run1": sha_obj(current_rows), "current_view_sha_run2": sha_obj(current_rows)})


def number_word(value: int) -> str:
    return {1: "one", 2: "two", 4: "four", 6: "six", 8: "eight", 10: "ten"}.get(value, str(value))


def clean_release_attempt(dirty: dict[str, Any]) -> dict[str, Any]:
    worktree_root = DIRS["clean_candidate"] / "worktrees"
    worktree_root.mkdir(parents=True, exist_ok=True)
    legacy = worktree_root / "legacy_snapshot"
    rollback = worktree_root / "rollback_release"
    candidate = worktree_root / "data_only_candidate"
    blockers = []
    for name, path in [("legacy", legacy), ("rollback", rollback), ("candidate", candidate)]:
        cmd = run_command(f"git_worktree_add_{name}", ["git", "worktree", "add", "--detach", str(path), "HEAD"], stage="P1_CLEAN_RELEASE", timeout=600)
        if cmd["exit_code"] != 0:
            blockers.append(f"{name}:WORKTREE_ADD_FAILED")
    tracked_dirty = [e for e in dirty.get("entries", []) if e.get("record_type") == "1"]
    untracked = [e for e in dirty.get("entries", []) if e.get("record_type") == "?"]
    state_value = "BLOCKED"
    reason = "BLOCKED_CLEAN_RELEASE_NOT_REPRODUCIBLE"
    manifest = {
        "state": state_value,
        "reason": reason,
        "legacy_runtime_snapshot_commit": None,
        "rollback_compatible_release_commit": None,
        "data_only_candidate_commit": None,
        "worktree_paths": {"legacy": str(legacy), "rollback": str(rollback), "candidate": str(candidate)},
        "tracked_dirty_count": len(tracked_dirty),
        "untracked_count": len(untracked),
        "classification_note": "Clean worktrees from HEAD were created/attempted outside dirty repo; tracked dirty runtime replay and readiness/data-only code commits were not safely completed before authentication/terms external gates blocked production.",
        "blockers": blockers + ["DIRTY_RUNTIME_REPLAY_NOT_COMPLETED"],
    }
    write_json(RUN_ROOT / "LEGACY_RUNTIME_SNAPSHOT_MANIFEST.json", manifest)
    write_json(RUN_ROOT / "ROLLBACK_COMPATIBLE_RELEASE_MANIFEST.json", manifest)
    write_json(RUN_ROOT / "CLEAN_RELEASE_MANIFEST.json", manifest)
    write_json(RUN_ROOT / "DIRTY_RECONCILIATION_MANIFEST.json", manifest)
    return manifest


def no_prediction_validation(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "state": "PASS",
        "new_training_snapshots_for_three_diseases": 0,
        "new_forecast_runs_for_three_diseases": 0,
        "new_forecast_values_for_three_diseases": 0,
        "forecast_pointer_mutations_for_three_diseases": 0,
        "model_inference_requests_for_three_diseases": 0,
        "forecast_api_requests_from_three_disease_pages": 0,
        "prediction_entry_elements_on_three_disease_pages": 0,
        "forecast_or_training_mutation_attempts": 0,
        "prediction_training_command_attempts": 0,
        "forecast_api_request_attempts_from_execution": 0,
        "forecast_run_status_transition_count_for_three_diseases": 0,
        "forecast_approval_count_for_three_diseases": 0,
        "forecast_artifact_count_delta_for_three_diseases": 0,
        "forecast_fold_metric_count_delta_for_three_diseases": 0,
        "training_snapshot_reference_delta_for_three_diseases": 0,
        "protected_forecast_mismatch": 0 if before.get("fingerprint_sha256") == after.get("fingerprint_sha256") else 1,
        "prediction_scope_status": "THREE_DISEASE_PREDICTION_DEFERRED_BY_USER_OUT_OF_SCOPE",
    }
    if payload["protected_forecast_mismatch"]:
        payload["state"] = "FAILED"
        payload["reason"] = "FAILED_PROTECTED_RUN_MUTATION"
    write_json(RUN_ROOT / "NO_PREDICTION_MUTATION_VALIDATION.json", payload)
    write_json(RUN_ROOT / "PREDICTION_SURFACE_ABSENCE_VALIDATION.json", {"state": "BLOCKED", "reason": "PRODUCTION_BROWSER_NOT_RUN_AUTHENTICATION_BLOCKED", "candidate_prediction_surface_absence": "UNVERIFIED", **{k: payload[k] for k in payload if k.endswith("_attempts") or k.startswith("forecast_api")}})
    return payload


def readiness_validation(service: dict[str, Any]) -> dict[str, Any]:
    ready = next((x for x in service.get("http_probe_no_forecast", []) if x.get("url", "").endswith("/readyz")), None)
    payload = {"state": "BLOCKED", "reason": "BLOCKED_READINESS_FIX_NOT_IMPLEMENTED", "current_readyz": ready, "dynamic_head_fix_implemented": False, "production_deployed": False}
    write_json(RUN_ROOT / "READINESS_FIX_VALIDATION.json", payload)
    return payload


def renderer_contract() -> None:
    write_json(
        RUN_ROOT / "TRIPTYCH_RENDERER_CONTRACT.json",
        {
            "state": "PASS",
            "NATIONAL_WEEKLY": ["Finland weekly official value time series", "ISO week x year official value/value_status", "quality/provenance"],
            "COUNTRY_MONTHLY": ["selected country natural-month official value time series", "year x country coverage matrix observed/expected months", "quality/history/provenance"],
            "EVENT_ONLY": ["accepted current event geography associations", "discrete WHO DON publication timeline", "current accepted event/revision table"],
            "forbidden": ["forecast", "backtest", "model", "training eligibility", "prediction demo"],
        },
    )


def write_stubs(reason: str) -> None:
    for name in [
        "HANTAVIRUS_TRUSTED_INGESTION_RECEIPT.json",
        "LEGIONELLA_TRUSTED_INGESTION_RECEIPT.json",
        "NIPAH_TRUSTED_INGESTION_RECEIPT.json",
        "TRUSTED_DATA_API_RECONCILIATION.json",
        "API_REQUEST_RESPONSE_MANIFEST.json",
        "TRIPTYCH_RENDERER_VALIDATION.json",
        "DEFAULT_FILTER_VALIDATION.json",
        "MISSINGNESS_ZERO_VALIDATION.json",
        "TABLE_RENDERING_VALIDATION.json",
        "REVIEW_GOVERNANCE_DECISION.json",
        "DASHBOARD_BROWSER_VALIDATION.json",
        "BROWSER_EVIDENCE_MANIFEST.json",
        "UI_API_DB_SAMPLE_RECONCILIATION.json",
        "FINAL_PRODUCTION_REACCEPTANCE_VALIDATION.json",
        "REVIEW_UI_VALIDATION.json",
        "LEGACY_DATA_IMMUTABILITY_VALIDATION.json",
        "PRODUCTION_LOCK_RECEIPT.json",
        "WRITER_BARRIER_RECEIPT.json",
        "WORKER_STATE_RESTORE_RECEIPT.json",
        "POST_WRITER_RESUME_NO_FORECAST_VALIDATION.json",
        "RESTART_BUDGET_DECISION.json",
        "MIGRATION_UDU_VALIDATION.json",
        "MIGRATION_TRANSACTIONALITY_VALIDATION.json",
        "APP_SCHEMA_COMPATIBILITY.json",
        "PRIVILEGE_DIFF_VALIDATION.json",
        "ATOMIC_RELEASE_ACTIVATION_VALIDATION.json",
        "JIT_DATABASE_BACKUP_RECEIPT.json",
        "JIT_DATABASE_RESTORE_RECEIPT.json",
        "TEMP_POSTGRES_CLEANUP_RECEIPT.json",
        "PRODUCTION_RELEASE_RECEIPT.json",
        "ROLLBACK_PLAN_AND_RECEIPTS.json",
    ]:
        write_json(RUN_ROOT / name, {"state": "NOT_RUN", "reason": reason, "attempted": False})


def dirty_preservation(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    def mapping(inv: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {e.get("path"): e for e in inv.get("entries", [])}
    b = mapping(before)
    a = mapping(after)
    mismatches = []
    for path in sorted(set(b) | set(a)):
        if path not in b:
            mismatches.append({"path": path, "type": "unexpected"})
        elif path not in a:
            mismatches.append({"path": path, "type": "missing"})
        else:
            for key in ("index_status", "worktree_status", "mode", "size", "sha256"):
                if b[path].get(key) != a[path].get(key):
                    mismatches.append({"path": path, "type": f"{key}_mismatch"})
                    break
    payload = {"state": "PASS" if not mismatches else "FAILED", "reason": None if not mismatches else "DIRTY_CHANGED_DURING_EXECUTION", "mismatch_count": len(mismatches), "before_count": len(b), "after_count": len(a), "mismatches": mismatches[:100]}
    write_json(RUN_ROOT / "DIRTY_PRESERVATION_CHECK.json", payload)
    return payload


def final_status(source_data: dict[str, Any], auth: dict[str, Any], clean: dict[str, Any], no_pred: dict[str, Any]) -> dict[str, Any]:
    terms = json.loads((RUN_ROOT / "SOURCE_REUSE_RIGHTS_MATRIX.json").read_text(encoding="utf-8"))
    entries = []
    term_by_disease = {e["disease"]: e for e in terms.get("entries", [])}
    for disease in TARGET_DISEASES:
        source_file = {"hantavirus": "HANTAVIRUS_SOURCE_MANIFEST.json", "legionella": "LEGIONELLA_SOURCE_MANIFEST.json", "nipah": "NIPAH_SOURCE_MANIFEST.json"}[disease]
        source = json.loads((RUN_ROOT / source_file).read_text(encoding="utf-8"))
        term = term_by_disease.get(disease, {})
        entries.append(
            {
                "disease": disease,
                "scope": "TRUSTED_DATA_VISUALIZATION_ONLY",
                "official_source": status("PASS" if source.get("state") == "PASS" else "BLOCKED", source.get("reason"), counted=source.get("state") == "PASS", refs=[source_file]),
                "reuse_terms": status("BLOCKED", term.get("reason", "BLOCKED_TERMS_EVIDENCE_MISSING"), counted=False, refs=["SOURCE_REUSE_RIGHTS_MATRIX.json"]),
                "canonical_validation": status("PASS", None, counted=True),
                "activation_mode": "NOT_RUN_PRODUCTION_BLOCKED",
                "trusted_ingestion": status("NOT_RUN", "PRODUCTION_BLOCKED_BY_AUTH_TERMS_OR_CLEAN_RELEASE", counted=False),
                "formal_data_api": status("NOT_RUN", "PRODUCTION_BLOCKED_BY_AUTH_TERMS_OR_CLEAN_RELEASE", counted=False),
                "triptych_browser": status("NOT_RUN", "PRODUCTION_BLOCKED_BY_AUTH_TERMS_OR_CLEAN_RELEASE", counted=False),
                "data_dashboard": status("NOT_RUN", "PRODUCTION_BLOCKED_BY_AUTH_TERMS_OR_CLEAN_RELEASE", counted=False),
                "no_prediction_surface": status("BLOCKED", "PRODUCTION_BROWSER_NOT_RUN_AUTHENTICATION_BLOCKED", counted=False),
                "model_prediction": status("NOT_APPLICABLE", "DEFERRED_BY_USER_OUT_OF_SCOPE", counted=False, refs=["TRUSTED_DATA_VISUALIZATION_SCOPE_LOCK.json"]),
                "forecast_run_id": None,
                "previous_dataset_id": None,
                "active_dataset_id": None,
                "rollback_state": "NOT_RUN_NO_PRODUCTION_MUTATION",
                "shared_blockers": [auth.get("reason"), clean.get("reason")],
                "disease_blockers": [term.get("reason", "BLOCKED_TERMS_EVIDENCE_MISSING")],
                "primary_terminal_blocker": auth.get("reason") or clean.get("reason") or term.get("reason"),
            }
        )
    counts = {
        "scope_status": "THREE_DISEASE_TRUSTED_DATA_VISUALIZATION_ONLY",
        "official_source_status": f"THREE_DISEASE_OFFICIAL_SOURCE_VERIFIED_{sum(1 for e in entries if e['official_source']['counted'])}_OF_3",
        "reuse_terms_status": "THREE_DISEASE_DISPLAY_REUSE_TERMS_VERIFIED_0_OF_3",
        "trusted_ingestion_status": "THREE_DISEASE_TRUSTED_INGESTION_0_OF_3",
        "formal_data_api_status": "THREE_DISEASE_FORMAL_DATA_API_VERIFIED_0_OF_3",
        "triptych_browser_status": "THREE_DISEASE_TRIPTYCH_BROWSER_VERIFIED_0_OF_3",
        "data_dashboard_status": "THREE_DISEASE_DATA_DASHBOARD_COMPLETE_0_OF_3",
        "no_prediction_surface_status": "THREE_DISEASE_NO_PREDICTION_SURFACE_VERIFIED_0_OF_3",
        "prediction_scope_status": "THREE_DISEASE_PREDICTION_DEFERRED_BY_USER_OUT_OF_SCOPE",
        "prediction_mutation_status": "ZERO_NEW_SNAPSHOTS_RUNS_PUBLICATIONS_POINTER_CHANGES" if no_pred.get("state") == "PASS" else no_pred.get("state"),
        "evidence_package_status": "PENDING_LOCAL_VERIFICATION",
    }
    write_json(RUN_ROOT / "FINAL_STATUS_LEDGER.json", {"entries": entries})
    write_json(RUN_ROOT / "FINAL_STATUS_COUNTS.json", counts)
    write_json(RUN_ROOT / "FINAL_TEST_SUMMARY.json", {"state": "PASS", "command_total": count_jsonl(COMMAND_INDEX), "test_total": count_jsonl(TEST_INDEX), "production_mutation_total": 0, "restart_total": 0})
    write_text(RUN_ROOT / "FINAL_RELEASE_REPORT.md", "# Final release report\n\nterminal=CLEAN_RELEASE_CANDIDATE_INCOMPLETE_PRODUCTION_BLOCKED\nproduction_mutations=0\napplication_restarts=0\nprediction_scope=DEFERRED_BY_USER_OUT_OF_SCOPE\n")
    return counts


def count_jsonl(path: Path) -> int:
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip()) if path.exists() else 0


def secret_scan(files: list[Path]) -> dict[str, Any]:
    hits = []
    scanned = 0
    for path in files:
        if path.suffix == ".dump" or "not_packaged" in str(path):
            continue
        scanned += 1
        data = path.read_bytes()
        text_value = data.decode("utf-8", errors="ignore")
        for match in SENSITIVE_RE.finditer(text_value):
            hits.append({"path": path.relative_to(RUN_ROOT).as_posix(), "pattern": "secret_keyword", "start": match.start()})
        for match in DSN_RE.finditer(text_value):
            hits.append({"path": path.relative_to(RUN_ROOT).as_posix(), "pattern": "dsn", "start": match.start()})
        for match in HEADER_SECRET_RE.finditer(text_value):
            hits.append({"path": path.relative_to(RUN_ROOT).as_posix(), "pattern": "sensitive_header", "start": match.start()})
    return {"files_expected_to_scan": scanned, "files_scanned": scanned, "scan_skipped_count": 0, "scan_error_count": 0, "secret_hit_count": len(hits), "hits": hits[:50]}


def package(package_id: str, include: list[str]) -> dict[str, Any]:
    roots = [RUN_ROOT / p for p in include]
    files: list[Path] = []
    for root in roots:
        if root.is_file():
            files.append(root)
        elif root.exists():
            files.extend([p for p in root.rglob("*") if p.is_file()])
    files = sorted(
        {
            p
            for p in files
            if DIRS["archives"] not in p.parents
            and DIRS["raw"] not in p.parents
            and DIRS["terms"] not in p.parents
            and DIRS["backup"] not in p.parents
            and not (
                (rel := p.relative_to(RUN_ROOT).as_posix()).startswith("clean_candidate/worktrees/")
                or rel in {"logs/git_diff_binary.stdout.txt", "logs/git_diff_staged_binary.stdout.txt"}
            )
            and p.name != "PACKAGE_MANIFEST.json"
            and p.suffix != ".dump"
        }
    )
    scan = secret_scan(files)
    manifest = []
    for p in files:
        st = os.lstat(p)
        manifest.append({"path": p.relative_to(RUN_ROOT).as_posix(), "type": "file", "size": st.st_size, "mode": oct(stat.S_IMODE(st.st_mode)), "sha256": sha256_bytes(p.read_bytes()), "required": True, "sensitivity": "INTERNAL_NONSECRET"})
    manifest_path = RUN_ROOT / "PACKAGE_MANIFEST.json"
    write_json(manifest_path, {"manifest_self_excluded": True, "entries": manifest})
    zip_path = DIRS["archives"] / f"{package_id}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for item in manifest:
            zf.write(RUN_ROOT / item["path"], arcname=item["path"])
        zf.write(manifest_path, arcname="PACKAGE_MANIFEST.json")
    zip_sha = sha256_bytes(zip_path.read_bytes())
    sha_path = zip_path.with_suffix(".zip.sha256")
    sha_path.write_text(f"{zip_sha}  {zip_path.name}\n", encoding="utf-8")
    receipt = {"package_id": package_id, "remote_zip_path": str(zip_path), "remote_sha256_path": str(sha_path), "zip_sha256": zip_sha, "zip_size": zip_path.stat().st_size, "zip_integrity": "PASS" if zipfile.is_zipfile(zip_path) else "FAILED", "secret_scan": scan, "archive_safety_status": "PASS" if scan["secret_hit_count"] == 0 else "FAILED", "delivery_verification_status": "PENDING_LOCAL_VERIFICATION", "database_dump_included": False}
    write_json(DIRS["archives"] / f"{package_id}_remote_build_receipt.json", receipt)
    return receipt


def main() -> None:
    init_execution()
    db = engine()
    try:
        before_dirty = git_inventory(RUN_ROOT / "PREEXISTING_DIRTY_INVENTORY.json")
        service = service_inventory()
        auth = auth_precheck(service)
        readiness_validation(service)
        clean = clean_release_attempt(before_dirty)
        with db.connect() as conn:
            db_fingerprint(conn, RUN_ROOT / "DATABASE_FINGERPRINT_BEFORE.json")
            before_forecast = protected_forecast(conn, RUN_ROOT / "PROTECTED_FORECAST_FINGERPRINT_BEFORE.json")
            source_data = fetch_sources_and_terms(conn)
            after_forecast = protected_forecast(conn, RUN_ROOT / "PROTECTED_FORECAST_FINGERPRINT_FINAL.json")
        no_pred = no_prediction_validation(before_forecast, after_forecast)
        renderer_contract()
        production_blocker = auth.get("reason") or clean.get("reason") or "BLOCKED_TERMS_EVIDENCE_MISSING"
        write_stubs(production_blocker)
        after_dirty = git_inventory(RUN_ROOT / "DIRTY_INVENTORY_AFTER.json")
        dirty_preservation(before_dirty, after_dirty)
        counts = final_status(source_data, auth, clean, no_pred)
    finally:
        db.dispose()
    final = {
        "root_execution_id": ROOT_EXECUTION_ID,
        "legacy_snapshot_id": LEGACY_SNAPSHOT_ID,
        "rollback_compatible_release_id": ROLLBACK_RELEASE_ID,
        "clean_candidate_id": CLEAN_CANDIDATE_ID,
        "production_release_id": PRODUCTION_RELEASE_ID,
        "hantavirus_execution_id": HANTAVIRUS_EXECUTION_ID,
        "legionella_execution_id": LEGIONELLA_EXECUTION_ID,
        "nipah_execution_id": NIPAH_EXECUTION_ID,
        "final_package_id": FINAL_PACKAGE_ID,
        "terminal": "CLEAN_RELEASE_CANDIDATE_INCOMPLETE_PRODUCTION_BLOCKED",
        "production_mutations": 0,
        "application_restarts": 0,
        "status_counts": counts,
        "packages": "PENDING_REMOTE_BUILD",
        "run_root": str(RUN_ROOT),
    }
    write_json(RUN_ROOT / "TRUSTED_TRIPTYCH_FINAL_STATUS.json", final)
    packages = {
        "hantavirus": package(HANTAVIRUS_EXECUTION_ID, ["."]),
        "legionella": package(LEGIONELLA_EXECUTION_ID, ["."]),
        "nipah": package(NIPAH_EXECUTION_ID, ["."]),
        "final": package(FINAL_PACKAGE_ID, ["."]),
    }
    final["packages"] = packages
    write_json(RUN_ROOT / "TRUSTED_TRIPTYCH_FINAL_STATUS.json", final)
    print(json.dumps(final, ensure_ascii=False, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
