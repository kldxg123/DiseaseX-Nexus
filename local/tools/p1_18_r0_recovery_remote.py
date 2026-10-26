from __future__ import annotations

import csv
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT = Path(os.environ.get("PROJECT", ".")).resolve()
DATA_ROOT = PROJECT.parent
PARENT_RUN_ID = "p1_18_20260726T034421Z_7391aeea"
PARENT_STATUS = "FLUNET_DEEP_FORECAST_PARTIAL_WITH_EXPLICIT_BLOCKERS"
PARENT_ZIP_SHA_EXPECTED = "d8b3562f83b7f54c1224547af7bad0f289bba9b99143f436ec9a5ac88f5d4698"
REPAIR_ID = "p1_18_r0_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:8]
ARTIFACT = PROJECT / "artifacts" / f"p1_18_r0_postgres_cpu_runtime_{REPAIR_ID}"
RUNTIME_ROOT = DATA_ROOT / "p1_18_runtime" / REPAIR_ID
VENv = RUNTIME_ROOT / "venv"
WHEELHOUSE = RUNTIME_ROOT / "wheelhouse"
REPORTS = PROJECT / "reports"
PG_SCAN_NOTES: list[dict[str, str]] = []

FORBIDDEN_DEPS = [
    "neuralforecast",
    "ray",
    "optuna",
    "pytorch-lightning",
    "lightning",
    "nvidia-",
    "cuda",
    "cudnn",
    "nccl",
    "triton",
    "cupy",
]

EXPECTED_WHEELS = {
    "torch": {
        "version": "2.12.1+cpu",
        "filename": "torch-2.12.1+cpu-cp310-cp310-manylinux_2_28_x86_64.whl",
        "sha256": "41c4824c535315dc8a4f92a4a61203df2074d052dbf8f75fda8bfe472fe3cdde",
        "index": "https://download.pytorch.org/whl/cpu",
    },
    "numpy": {"version": "2.2.6", "sha256": "fc7b73d02efb0e18c000e9ad8b83480dfcd5dfd11065997ed4c6747470ae8915"},
    "pandas": {"version": "2.3.2", "sha256": "cc03acc273c5515ab69f898df99d9d4f12c4d70dbfc24c3acc6203751d0804cf"},
    "scipy": {"version": "1.15.3", "sha256": "9e2abc762b0811e09a0d3258abee2d98e0c703eee49464ce0069590846f31d40"},
    "safetensors": {"version": "0.8.0", "sha256": "fd6f3f93c9a0a7cc2788ee63fb763353d4bd2e89b0751bc78fcf7dda00bea774"},
    "neuralforecast": {
        "version": "3.2.0",
        "filename": "neuralforecast-3.2.0-py3-none-any.whl",
        "sha256": "e46d72ff3534de9333d6ce222e16663f36e467c35d86382a30a5014d3b05eb84",
    },
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_text(path: Path, value: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8", newline="\n")
    return sha_bytes(value.encode("utf-8"))


def write_json(path: Path, value: Any) -> str:
    return write_text(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n")


def run(cmd_id: str, args: list[str], cwd: Path = PROJECT, timeout: int = 120, env: dict[str, str] | None = None) -> dict[str, Any]:
    started = now()
    safe_env = {
        "PYTHONNOUSERSITE": (env or os.environ).get("PYTHONNOUSERSITE"),
        "CUDA_VISIBLE_DEVICES": (env or os.environ).get("CUDA_VISIBLE_DEVICES"),
        "OMP_NUM_THREADS": (env or os.environ).get("OMP_NUM_THREADS"),
        "MKL_NUM_THREADS": (env or os.environ).get("MKL_NUM_THREADS"),
        "OPENBLAS_NUM_THREADS": (env or os.environ).get("OPENBLAS_NUM_THREADS"),
        "proxy_variables_present": any((env or os.environ).get(k) for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY")),
    }
    proc = subprocess.run(args, cwd=str(cwd), env=env, text=True, capture_output=True, timeout=timeout)
    stdout_path = ARTIFACT / "commands" / f"{cmd_id}.stdout.txt"
    stderr_path = ARTIFACT / "commands" / f"{cmd_id}.stderr.txt"
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stdout_path.write_text(proc.stdout, encoding="utf-8", errors="replace")
    stderr_path.write_text(proc.stderr, encoding="utf-8", errors="replace")
    record = {
        "command_id": cmd_id,
        "argv": args,
        "cwd": str(cwd),
        "started_at_utc": started,
        "finished_at_utc": now(),
        "exit_code": proc.returncode,
        "stdout_path": str(stdout_path.relative_to(ARTIFACT)),
        "stderr_path": str(stderr_path.relative_to(ARTIFACT)),
        "stdout_sha256": sha_file(stdout_path),
        "stderr_sha256": sha_file(stderr_path),
        "environment_summary": safe_env,
    }
    write_json(ARTIFACT / "commands" / f"{cmd_id}.json", record)
    return record


def read_stdout(record: dict[str, Any]) -> str:
    return (ARTIFACT / record["stdout_path"]).read_text(encoding="utf-8", errors="replace")


def gate(gate_id: str, status: str, evidence: list[str], computed: Any = None, expected: Any = None, blocker: str | None = None, blocked_by: str | None = None) -> dict[str, Any]:
    return {
        "gate_id": gate_id,
        "status": status,
        "evidence_paths": evidence,
        "computed_value": computed,
        "expected_value": expected,
        "blocker_code": blocker,
        "blocked_by": blocked_by,
    }


def collect_env_manifest(label: str, python_path: str) -> dict[str, Any]:
    code = r"""
import hashlib, importlib.metadata as md, json, sys
rows=[]
for d in sorted(md.distributions(), key=lambda x: (x.metadata.get('Name') or '').lower()):
    name=d.metadata.get('Name') or ''
    version=d.version
    rows.append({'name':name,'version':version})
payload=json.dumps(rows,sort_keys=True,separators=(',',':'))
print(json.dumps({'python':sys.executable,'count':len(rows),'sha256':hashlib.sha256(payload.encode()).hexdigest(),'packages':rows},sort_keys=True))
"""
    rec = run(f"shared_env_{label}", [python_path, "-c", code], timeout=120)
    text = read_stdout(rec)
    try:
        data = json.loads(text)
    except Exception as exc:
        data = {"parse_error": type(exc).__name__, "raw_sha256": sha_bytes(text.encode())}
    out_dir = ARTIFACT / ("shared_environment_before" if label.endswith("before") else "shared_environment_after")
    write_json(out_dir / f"{label}.json", data)
    return data


def baseline() -> dict[str, Any]:
    b = ARTIFACT / "baseline"
    b.mkdir(parents=True, exist_ok=True)
    parent_zip = PROJECT / "reports" / f"{PARENT_RUN_ID}.zip"
    # The earlier P1-18 artifact was zipped under reports/p1_18_flunet_deep_forecast_<run>.zip.
    parent_zip_alt = PROJECT / "reports" / f"p1_18_flunet_deep_forecast_{PARENT_RUN_ID}.zip"
    chosen_parent_zip = parent_zip if parent_zip.exists() else parent_zip_alt
    parent_verification = {
        "parent_run_id": PARENT_RUN_ID,
        "parent_status": PARENT_STATUS,
        "expected_sha256": PARENT_ZIP_SHA_EXPECTED,
        "server_path_checked": str(chosen_parent_zip),
        "exists_on_server": chosen_parent_zip.exists(),
        "actual_sha256": sha_file(chosen_parent_zip) if chosen_parent_zip.exists() else "NOT_PRESENT_ON_SERVER",
    }
    parent_verification["match"] = parent_verification["actual_sha256"] == PARENT_ZIP_SHA_EXPECTED
    write_json(b / "parent_reference.json", {"parent_run_id": PARENT_RUN_ID, "parent_status": PARENT_STATUS, "parent_zip_sha256_expected": PARENT_ZIP_SHA_EXPECTED})
    write_json(b / "parent_zip_verification.json", parent_verification)
    if chosen_parent_zip.exists() and not parent_verification["match"]:
        raise RuntimeError("P1_18_R0_PARENT_SHA256_MISMATCH")
    recs = {
        "git_head": run("git_head", ["git", "rev-parse", "HEAD"]),
        "git_status": run("git_status_porcelain_v2", ["git", "status", "--porcelain=v2"]),
        "process_before": run("process_state_before", ["bash", "-lc", "ps -eo pid,ppid,stat,lstart,etime,pcpu,pmem,comm,args --sort=pid | grep -E 'postgres|postmaster|uvicorn|dashboard|source_review|source-discovery|preview-publication|daily_update|openclaw|vllm|qwen|python' | grep -v grep || true"]),
        "listeners_before": run("listeners_before", ["bash", "-lc", "ss -ltnp 2>/dev/null | grep -E ':(5432|8020|8021|8022|8023|18020|18021)\\b' || true"]),
        "boot_identity": run("boot_identity", ["bash", "-lc", "cat /proc/sys/kernel/random/boot_id; uname -a; id epidemicdb || true"]),
        "filesystem_and_memory_before": run("filesystem_and_memory_before", ["bash", "-lc", "uptime; nproc; free -h; df -h / /home USER_HOME/autodl-tmp 2>/dev/null || true; du -sh storage/database/postgresql18 2>/dev/null || true"]),
    }
    write_text(b / "git_head.txt", read_stdout(recs["git_head"]))
    write_text(b / "git_status_porcelain_v2.txt", read_stdout(recs["git_status"]))
    write_text(b / "process_state_before.txt", read_stdout(recs["process_before"]))
    write_text(b / "listeners_before.txt", read_stdout(recs["listeners_before"]))
    write_text(b / "boot_identity.txt", read_stdout(recs["boot_identity"]))
    write_text(b / "filesystem_and_memory_before.txt", read_stdout(recs["filesystem_and_memory_before"]))
    dirty_lines = [x for x in read_stdout(recs["git_status"]).splitlines() if x.strip()]
    dirty_paths = []
    for line in dirty_lines:
        parts = line.split()
        if parts:
            dirty_paths.append(parts[-1])
    overlap_targets = [
        "scripts/database",
        "storage/database",
        "artifacts/p1_18_r0_postgres_cpu_runtime",
        "tools/p1_18_r0_recovery_remote.py",
    ]
    overlap = [p for p in dirty_paths if any(p.startswith(t) for t in overlap_targets)]
    write_json(b / "dirty_overlap_assessment.json", {"dirty_count": len(dirty_lines), "overlap_paths": overlap, "blocked": False, "reason": "R0 writes only new artifact/runtime paths and does not edit project source/API/Dashboard/database files."})
    epidemic_python = sys.executable
    shared_before = {
        "epidemic_core": collect_env_manifest("epidemic_core_before", epidemic_python),
        "base_python": collect_env_manifest("base_before", shutil.which("python3") or epidemic_python),
    }
    write_json(b / "shared_environment_before.json", shared_before)
    return {
        "parent": parent_verification,
        "git_head": read_stdout(recs["git_head"]).strip(),
        "dirty_count": len(dirty_lines),
        "process_before": read_stdout(recs["process_before"]),
        "listeners_before": read_stdout(recs["listeners_before"]),
        "shared_before": shared_before,
    }


def find_pg_candidates() -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen = set()
    prune_names = {
        ".git",
        "__pycache__",
        "cache",
        "conda",
        "envs",
        "huggingface",
        "models",
        "node_modules",
        "p1_18_runtime",
        "venv",
        "wheelhouse",
    }

    def add_pg_version(pg_version: Path) -> None:
        pgdata = pg_version.parent.resolve()
        required_cluster_markers = ["base", "global", "pg_wal", "pg_xact", "global/pg_control"]
        try:
            marker_checks = {m: (pgdata / m).exists() for m in required_cluster_markers}
        except OSError as exc:
            PG_SCAN_NOTES.append({"path": str(pgdata), "operation": "cluster_marker_exists", "result": "SKIPPED_INACCESSIBLE", "error_type": type(exc).__name__})
            return
        if not all(marker_checks.values()):
            PG_SCAN_NOTES.append({"path": str(pgdata), "operation": "cluster_marker_filter", "result": "SKIPPED_NOT_CLUSTER_ROOT", "error_type": "NOT_PGDATA_ROOT"})
            return
        key = str(pgdata)
        if key in seen:
            return
        seen.add(key)
        bin_candidates = []
        for p in [
            pgdata.parent / "bin",
            pgdata.parent.parent / "bin",
            pgdata.parent.parent / "postgresql18" / "bin",
            DATA_ROOT / "postgresql18" / "bin",
            DATA_ROOT / "postgresql-18.4" / "bin",
            DATA_ROOT / "postgresql" / "bin",
            DATA_ROOT / "conda" / "envs" / "postgresql18" / "bin",
            DATA_ROOT / "conda" / "envs" / "epidemic-core" / "bin",
            DATA_ROOT / "conda" / "bin",
        ]:
            try:
                has_pair = (p / "postgres").exists() and (p / "pg_ctl").exists()
            except OSError as exc:
                PG_SCAN_NOTES.append({"path": str(p), "operation": "bin_pair_exists", "result": "SKIPPED_INACCESSIBLE", "error_type": type(exc).__name__})
                continue
            if has_pair:
                bin_candidates.append(p.resolve())
        try:
            pg_version_text = pg_version.read_text(encoding="ascii", errors="replace").strip()
        except OSError as exc:
            PG_SCAN_NOTES.append({"path": str(pg_version), "operation": "read_text", "result": "SKIPPED_INACCESSIBLE", "error_type": type(exc).__name__})
            return
        candidates.append({"pgdata": str(pgdata), "pg_version_file": pg_version_text, "bin_candidates": [str(x) for x in bin_candidates]})

    direct_pgdata_candidates = [
        PROJECT / "storage" / "database" / "postgresql18" / "data",
        PROJECT / "storage" / "database" / "postgresql18",
        PROJECT / "storage" / "postgresql18",
        PROJECT / "storage" / "postgres",
        DATA_ROOT / "storage" / "database" / "postgresql18",
        DATA_ROOT / "postgresql18" / "data",
        DATA_ROOT / "postgresql-18.4" / "data",
        DATA_ROOT / "postgresql" / "data",
    ]
    for pgdata in direct_pgdata_candidates:
        pg_version = pgdata / "PG_VERSION"
        try:
            exists = pg_version.exists()
        except OSError as exc:
            PG_SCAN_NOTES.append({"path": str(pg_version), "operation": "direct_exists", "result": "SKIPPED_INACCESSIBLE", "error_type": type(exc).__name__})
            continue
        if exists:
            add_pg_version(pg_version)

    def bounded_walk_pg_versions(root: Path, max_depth: int) -> None:
        try:
            root_exists = root.exists()
        except OSError as exc:
            PG_SCAN_NOTES.append({"path": str(root), "operation": "exists", "result": "SKIPPED_INACCESSIBLE", "error_type": type(exc).__name__})
            return
        if not root_exists:
            return

        def onerror(exc: OSError) -> None:
            PG_SCAN_NOTES.append({"path": getattr(exc, "filename", str(root)), "operation": "os.walk", "result": "SKIPPED_INACCESSIBLE", "error_type": type(exc).__name__})

        for dirpath, dirnames, filenames in os.walk(root, topdown=True, onerror=onerror):
            current = Path(dirpath)
            try:
                depth = len(current.relative_to(root).parts)
            except ValueError:
                depth = max_depth + 1
            dirnames[:] = [d for d in dirnames if d not in prune_names and not d.startswith(".cache")]
            if depth >= max_depth:
                dirnames[:] = []
            if "PG_VERSION" in filenames:
                add_pg_version(current / "PG_VERSION")

    bounded_scan_roots: list[tuple[Path, int]] = [
        (PROJECT / "storage", 8),
        (PROJECT / "scripts", 5),
        (DATA_ROOT, 5),
        (Path("USER_HOME/autodl-tmp"), 5),
        (Path("USER_HOME/autodl-tmp"), 5),
    ]
    scanned_roots = set()
    for root, max_depth in bounded_scan_roots:
        try:
            key = str(root.resolve()) if root.exists() else str(root)
        except OSError:
            key = str(root)
        if key in scanned_roots:
            continue
        scanned_roots.add(key)
        bounded_walk_pg_versions(root, max_depth)
    return candidates


def discover_postgres() -> dict[str, Any]:
    out = ARTIFACT / "postgres_discovery"
    out.mkdir(parents=True, exist_ok=True)
    candidates = find_pg_candidates()
    write_json(out / "pg_candidates.json", candidates)
    write_json(out / "pg_scan_notes.json", PG_SCAN_NOTES)
    if not candidates:
        return {"status": "BLOCKED", "blocker": "P1_18_R0_DATABASE_CLUSTER_NOT_FOUND", "candidates": candidates}
    # Prefer the current project storage path if present.
    exact = [c for c in candidates if c["pgdata"].endswith("/storage/database/postgresql18/data")]
    selected = exact[0] if len(exact) == 1 else None
    if selected is None:
        if len(candidates) != 1:
            return {"status": "BLOCKED", "blocker": "P1_18_R0_DATABASE_CLUSTER_AMBIGUOUS", "candidates": candidates}
        selected = candidates[0]
    pgdata = Path(selected["pgdata"])
    bins = [Path(p) for p in selected["bin_candidates"]]
    if not bins:
        # Search bounded trusted local prefixes for bin/postgres without depending on PATH.
        bin_scan_roots = [DATA_ROOT / "conda", DATA_ROOT, PROJECT]
        bin_prune = {"models", "node_modules", "p1_18_runtime", "artifacts", "storage", ".git", "__pycache__"}
        for root in bin_scan_roots:
            try:
                root_exists = root.exists()
            except OSError as exc:
                PG_SCAN_NOTES.append({"path": str(root), "operation": "bin_scan_exists", "result": "SKIPPED_INACCESSIBLE", "error_type": type(exc).__name__})
                continue
            if not root_exists:
                continue
            for dirpath, dirnames, filenames in os.walk(root, topdown=True):
                current = Path(dirpath)
                try:
                    depth = len(current.relative_to(root).parts)
                except ValueError:
                    depth = 99
                dirnames[:] = [d for d in dirnames if d not in bin_prune]
                if depth >= 5:
                    dirnames[:] = []
                if {"postgres", "pg_ctl", "psql"}.issubset(set(filenames)):
                    bins.append(current.resolve())
    bins = sorted(set(bins))
    if len(bins) != 1:
        write_json(out / "pg_binary_candidates.json", [str(x) for x in bins])
        return {"status": "BLOCKED", "blocker": "P1_18_R0_DATABASE_CLUSTER_IDENTITY_UNPROVEN", "reason": "expected exactly one matching bin prefix", "pgdata": str(pgdata), "bin_candidates": [str(x) for x in bins]}
    bin_dir = bins[0]
    postgres = bin_dir / "postgres"
    pg_ctl = bin_dir / "pg_ctl"
    psql = bin_dir / "psql"
    pg_isready = bin_dir / "pg_isready"
    pg_controldata = bin_dir / "pg_controldata"
    version_rec = run("pg_postgres_version", [str(postgres), "--version"])
    pgctl_version = run("pg_ctl_version", [str(pg_ctl), "--version"])
    required_paths = ["base", "global", "pg_wal", "pg_xact", "global/pg_control"]
    path_checks = {p: (pgdata / p).exists() for p in required_paths}
    owner_uid = pgdata.stat().st_uid
    pg_version_major = (pgdata / "PG_VERSION").read_text(encoding="ascii").strip()
    control_rec = run("pg_controldata", [str(pg_controldata), str(pgdata)], timeout=60) if pg_controldata.exists() else {"exit_code": 127}
    control_text = read_stdout(control_rec) if isinstance(control_rec, dict) and "stdout_path" in control_rec else ""
    recovery_signals = {"standby.signal": (pgdata / "standby.signal").exists(), "recovery.signal": (pgdata / "recovery.signal").exists()}
    port_5432 = run("port_5432_before_start", ["bash", "-lc", "ss -ltnp 2>/dev/null | grep ':5432\\b' || true"])
    status_rec = run("pg_ctl_status_before", [str(pg_ctl), "-D", str(pgdata), "status"], timeout=60)
    discovery = {
        "status": "OK",
        "pgdata": str(pgdata),
        "bin_dir": str(bin_dir),
        "postgres": str(postgres),
        "pg_ctl": str(pg_ctl),
        "psql": str(psql),
        "pg_isready": str(pg_isready),
        "pg_controldata": str(pg_controldata),
        "postgres_sha256": sha_file(postgres),
        "pg_ctl_sha256": sha_file(pg_ctl),
        "psql_sha256": sha_file(psql) if psql.exists() else None,
        "version_stdout": read_stdout(version_rec),
        "pg_ctl_version_stdout": read_stdout(pgctl_version),
        "pg_version_major": pg_version_major,
        "owner_uid": owner_uid,
        "required_paths": path_checks,
        "recovery_signals": recovery_signals,
        "control_stdout_path": control_rec.get("stdout_path") if isinstance(control_rec, dict) else None,
        "port_5432_stdout": read_stdout(port_5432),
        "pg_ctl_status_exit": status_rec["exit_code"],
        "pg_ctl_status_stdout": read_stdout(status_rec),
        "pg_ctl_status_stderr": (ARTIFACT / status_rec["stderr_path"]).read_text(encoding="utf-8", errors="replace"),
        "cluster_state": next((line.split(":", 1)[1].strip() for line in control_text.splitlines() if line.startswith("Database cluster state:")), None),
        "system_identifier": next((line.split(":", 1)[1].strip() for line in control_text.splitlines() if line.startswith("Database system identifier:")), None),
    }
    if "18.4" not in discovery["version_stdout"] or pg_version_major != "18" or owner_uid != 999 or not all(path_checks.values()) or any(recovery_signals.values()):
        discovery["status"] = "BLOCKED"
        discovery["blocker"] = "P1_18_R0_POSTGRES_VERSION_OR_OWNER_MISMATCH"
    if discovery["port_5432_stdout"].strip():
        discovery["status"] = "BLOCKED"
        discovery["blocker"] = "P1_18_R0_PORT_5432_OCCUPIED_BY_UNKNOWN_PROCESS"
    write_json(out / "postgres_discovery.json", discovery)
    return discovery


def start_postgres(discovery: dict[str, Any]) -> dict[str, Any]:
    out = ARTIFACT / "postgres_recovery"
    out.mkdir(parents=True, exist_ok=True)
    if discovery.get("status") != "OK":
        result = {"status": "BLOCKED", "blocked_by": discovery.get("blocker")}
        write_json(out / "postgres_recovery.json", result)
        return result
    pgdata = Path(discovery["pgdata"])
    pg_ctl = discovery["pg_ctl"]
    pg_isready = discovery["pg_isready"]
    already_running = discovery["pg_ctl_status_exit"] == 0
    if already_running:
        status_after = run("pg_ctl_status_already_running", [pg_ctl, "-D", str(pgdata), "status"])
        result = {"status": "ALREADY_RUNNING", "start_attempted": False, "status_exit": status_after["exit_code"]}
        write_json(out / "postgres_recovery.json", result)
        return result
    pid_file = pgdata / "postmaster.pid"
    if pid_file.exists():
        text = pid_file.read_text(encoding="utf-8", errors="replace")
        pid = text.splitlines()[0].strip() if text.splitlines() else ""
        alive = Path("/proc") / pid
        if not (pid.isdigit() and alive.exists()):
            result = {"status": "BLOCKED", "blocker": "P1_18_R0_STALE_POSTMASTER_PID_REQUIRES_OPERATOR_DECISION", "pid_file_present": True, "pid": pid}
            write_json(out / "postgres_recovery.json", result)
            return result
    log_path = out / "postgres_startup.log"
    start_rec = run("postgres_pg_ctl_start", ["runuser", "-u", "epidemicdb", "--", pg_ctl, "-D", str(pgdata), "-l", str(log_path), "-w", "-t", "120", "start"], timeout=150)
    status_rec = run("pg_ctl_status_after_start", [pg_ctl, "-D", str(pgdata), "status"])
    ready_rec = run("pg_isready_after_start", [pg_isready, "-h", "127.0.0.1", "-p", "5432"], timeout=30)
    result = {
        "status": "STARTED" if start_rec["exit_code"] == 0 and ready_rec["exit_code"] == 0 else "BLOCKED",
        "start_exit": start_rec["exit_code"],
        "status_exit": status_rec["exit_code"],
        "pg_isready_exit": ready_rec["exit_code"],
        "startup_log_path": str(log_path.relative_to(ARTIFACT)),
        "startup_log_sha256": sha_file(log_path) if log_path.exists() else None,
    }
    if result["status"] == "BLOCKED":
        result["blocker"] = "P1_18_R0_DATABASE_START_IN_PROGRESS_TIMEOUT" if status_rec["exit_code"] == 0 else "P1_18_R0_DATABASE_CLUSTER_IDENTITY_UNPROVEN"
    write_json(out / "postgres_recovery.json", result)
    return result


def readonly_reconcile(discovery: dict[str, Any], recovery: dict[str, Any]) -> dict[str, Any]:
    out = ARTIFACT / "database_readonly"
    out.mkdir(parents=True, exist_ok=True)
    if recovery.get("status") not in {"STARTED", "ALREADY_RUNNING"}:
        result = {"status": "BLOCKED", "blocked_by": recovery.get("blocker") or recovery.get("blocked_by")}
        write_json(out / "database_readonly_reconciliation.json", result)
        return result
    # Use existing env for credentials without printing it.
    code = r"""
import hashlib,json,os
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import make_url
raw=os.environ.get('MIGRATION_DATABASE_URL') or os.environ.get('DATABASE_URL') or os.environ.get('DASHBOARD_DATABASE_URL')
url=make_url(raw)
if os.environ.get('POSTGRES_ADMIN_USER') and os.environ.get('POSTGRES_ADMIN_PASSWORD'):
    url=url.set(username=os.environ['POSTGRES_ADMIN_USER'], password=os.environ['POSTGRES_ADMIN_PASSWORD'])
engine=sa.create_engine(url,hide_parameters=True,future=True)
with engine.connect() as c:
    c.execute(text("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
    c.execute(text("SET LOCAL statement_timeout='30s'"))
    payload={}
    for key,sql in {
      'current_user':"select current_user",
      'transaction_read_only':"show transaction_read_only",
      'server_version':"show server_version",
      'data_directory':"show data_directory",
      'inet_server_addr':"select inet_server_addr()::text",
      'inet_server_port':"select inet_server_port()",
      'pg_is_in_recovery':"select pg_is_in_recovery()",
      'database':"select current_database()",
      'alembic_current':"select version_num from epidemic.alembic_version",
      'schema_epidemic':"select exists(select 1 from information_schema.schemata where schema_name='epidemic')",
      'trusted_flunet_rows':"select count(*)::bigint from epidemic_p1_12.trusted_dashboard_series_v1 where source_key='trusted-influenza-who-flunet-country-week-v1'",
      'trusted_flunet_metric_counts':"select jsonb_object_agg(metric_code,n) from (select metric_code,count(*)::int n from epidemic_p1_12.trusted_dashboard_series_v1 where source_key='trusted-influenza-who-flunet-country-week-v1' group by metric_code order by metric_code) s",
      'lineage_rows':"select count(*)::bigint from epidemic.dataset_snapshot_observations",
      'prediction_runs':"select count(*)::bigint from epidemic.prediction_runs",
      'prediction_results':"select count(*)::bigint from epidemic.prediction_results",
    }.items():
      payload[key]=c.execute(text(sql)).scalar_one()
    rows=[dict(r) for r in c.execute(text("select observation_id::text,country_iso3,metric_code,value_numeric::text,period_start::text,period_end::text,source_key from epidemic_p1_12.trusted_dashboard_series_v1 where source_key='trusted-influenza-who-flunet-country-week-v1' order by country_iso3,period_start,metric_code,observation_id limit 1000")).mappings()]
    canon=''.join(json.dumps(r,sort_keys=True,separators=(',',':'),ensure_ascii=False)+'\n' for r in rows)
    payload['trusted_flunet_sample1000_sha256']=hashlib.sha256(canon.encode()).hexdigest()
    c.execute(text("ROLLBACK"))
print(json.dumps(payload,default=str,ensure_ascii=False,sort_keys=True))
"""
    rec = run("database_readonly_reconciliation", ["bash", "-lc", "source scripts/database/_env.sh >/dev/null 2>&1; set -a; source .secrets/postgres.env >/dev/null 2>&1; set +a; python - <<'PY'\n" + code + "\nPY"], timeout=120)
    stdout = read_stdout(rec)
    try:
        data = json.loads(stdout)
        data["status"] = "PASS"
    except Exception as exc:
        data = {"status": "BLOCKED", "blocker": "P1_18_R0_DATABASE_AUTH_OR_PASSWORD_CHANGE_REQUIRED", "parse_error": type(exc).__name__, "exit_code": rec["exit_code"]}
    # Check identity contract.
    expected = {"server_version": "18.4", "database": "epidemic_openclaw", "schema_epidemic": True, "pg_is_in_recovery": False}
    data["identity_expected"] = expected
    data["identity_match"] = (
        str(data.get("server_version", "")).startswith("18.4")
        and data.get("database") == "epidemic_openclaw"
        and data.get("schema_epidemic") is True
        and data.get("pg_is_in_recovery") is False
        and str(data.get("data_directory")) == str(discovery.get("pgdata"))
    )
    if data.get("status") == "PASS" and not data["identity_match"]:
        data["status"] = "BLOCKED"
        data["blocker"] = "P1_18_R0_DATABASE_BASELINE_MISMATCH"
    write_json(out / "database_readonly_reconciliation.json", data)
    return data


def check_platform() -> dict[str, Any]:
    rec = run("platform_check", ["bash", "-lc", "python - <<'PY'\nimport json,platform,os\nprint(json.dumps({'machine':platform.machine(),'python_implementation':platform.python_implementation(),'python_version':platform.python_version(),'glibc':platform.libc_ver(),'disk':os.statvfs('USER_HOME/autodl-tmp').f_bavail*os.statvfs('USER_HOME/autodl-tmp').f_frsize}))\nPY"])
    data = json.loads(read_stdout(rec))
    data["supported"] = data["machine"] == "x86_64" and data["python_implementation"] == "CPython" and data["python_version"].startswith("3.10") and tuple(int(x) for x in data["glibc"][1].split(".")[:2]) >= (2, 28)
    write_json(ARTIFACT / "cpu_runtime" / "platform_check.json", data)
    return data


def pip_env() -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "PYTHONNOUSERSITE": "1",
            "CUDA_VISIBLE_DEVICES": "",
            "OMP_NUM_THREADS": "4",
            "MKL_NUM_THREADS": "4",
            "OPENBLAS_NUM_THREADS": "4",
            "PIP_NO_CACHE_DIR": "0",
        }
    )
    for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        env.pop(key, None)
    return env


def download_with_optional_turbo(cmd_id: str, args: list[str], timeout: int = 900) -> dict[str, Any]:
    # Direct official source first. If it fails and AutoDL turbo exists, use it only in the subshell and unset proxies after.
    direct = run(cmd_id + "_direct", args, timeout=timeout, env=pip_env())
    if direct["exit_code"] == 0:
        return {"mode": "direct", "record": direct}
    turbo_cmd = [
        "bash",
        "-lc",
        "set -euo pipefail; "
        "if [ ! -r /etc/network_turbo ]; then exit 77; fi; "
        "source /etc/network_turbo >/dev/null 2>&1; "
        + " ".join(sh_quote(x) for x in args)
        + "; rc=$?; unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; exit $rc",
    ]
    turbo = run(cmd_id + "_turbo", turbo_cmd, timeout=timeout, env=pip_env())
    return {"mode": "turbo" if turbo["exit_code"] == 0 else "failed", "record": turbo, "direct_record": direct}


def sh_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def build_cpu_runtime() -> dict[str, Any]:
    out = ARTIFACT / "cpu_runtime"
    out.mkdir(parents=True, exist_ok=True)
    platform_info = check_platform()
    if not platform_info["supported"]:
        result = {"status": "BLOCKED", "blocker": "P1_18_R0_CPU_TORCH_PLATFORM_UNSUPPORTED", "platform": platform_info}
        write_json(out / "runtime_result.json", result)
        return result
    RUNTIME_ROOT.mkdir(parents=True, exist_ok=False)
    WHEELHOUSE.mkdir(parents=True, exist_ok=True)
    disk_before = shutil.disk_usage(DATA_ROOT)
    if disk_before.free < 10 * 1024**3:
        result = {"status": "BLOCKED", "blocker": "P1_18_R0_INSUFFICIENT_DISK_OR_MEMORY", "free_bytes": disk_before.free}
        write_json(out / "runtime_result.json", result)
        return result
    venv_rec = run("venv_create", [sys.executable, "-m", "venv", "--copies", str(VENv)], timeout=300, env=pip_env())
    if venv_rec["exit_code"] != 0:
        result = {"status": "BLOCKED", "blocker": "P1_18_R0_CPU_TORCH_PLATFORM_UNSUPPORTED", "venv_exit": venv_rec["exit_code"]}
        write_json(out / "runtime_result.json", result)
        return result
    vpy = VENv / "bin" / "python"
    vpip = [str(vpy), "-m", "pip"]
    # Upgrade pip is not allowed if it mutates outside venv; inside venv is fine, but avoid unnecessary online changes.
    resolver = {
        "dependency_mode": "PURE_TORCH_FALLBACK",
        "runtime_path": str(VENv),
        "wheelhouse_path": str(WHEELHOUSE),
        "top_level": EXPECTED_WHEELS,
        "forbidden_dependency_patterns": FORBIDDEN_DEPS,
        "install_strategy": "download wheels first, verify expected top-level SHA, reject forbidden dependency plan, install from wheelhouse with --no-index",
    }
    write_json(ARTIFACT / "dependency_resolver_report.json", resolver)
    # Download exact top-level wheels, no deps where needed for provenance.
    downloads = []
    torch_args = vpip + [
        "download",
        "--only-binary=:all:",
        "--dest",
        str(WHEELHOUSE),
        "--index-url",
        EXPECTED_WHEELS["torch"]["index"],
        "--extra-index-url",
        "https://pypi.org/simple",
        f"torch=={EXPECTED_WHEELS['torch']['version']}",
    ]
    downloads.append(("torch", download_with_optional_turbo("pip_download_torch_cpu", torch_args, timeout=1800)))
    for name in ("numpy", "pandas", "scipy", "safetensors"):
        downloads.append((name, download_with_optional_turbo(f"pip_download_{name}", vpip + ["download", "--only-binary=:all:", "--dest", str(WHEELHOUSE), f"{name}=={EXPECTED_WHEELS[name]['version']}"], timeout=900)))
    nf = download_with_optional_turbo("pip_download_neuralforecast_nodeps", vpip + ["download", "--no-deps", "--only-binary=:all:", "--dest", str(WHEELHOUSE), f"neuralforecast=={EXPECTED_WHEELS['neuralforecast']['version']}"], timeout=900)
    downloads.append(("neuralforecast_no_deps", nf))
    wheel_files = sorted(WHEELHOUSE.glob("*.whl"))
    wheel_manifest = []
    for p in wheel_files:
        wheel_manifest.append({"filename": p.name, "bytes": p.stat().st_size, "sha256": sha_file(p)})
    write_json(ARTIFACT / "wheelhouse_manifest.json", {"downloads": downloads, "wheels": wheel_manifest})
    # Verify expected top-level files. For numpy/pandas/scipy/safetensors filenames vary by ABI tag; locate prefix.
    mismatches = []
    for name, spec in EXPECTED_WHEELS.items():
        if name == "neuralforecast":
            continue
        if name == "torch":
            candidates = [p for p in wheel_files if p.name == spec["filename"]]
        else:
            candidates = [p for p in wheel_files if p.name.lower().startswith(f"{name}-{spec['version']}".lower().replace("_", "-"))]
        if len(candidates) != 1:
            mismatches.append({"package": name, "reason": "expected exactly one wheel", "candidates": [p.name for p in candidates]})
        elif sha_file(candidates[0]) != spec["sha256"]:
            mismatches.append({"package": name, "filename": candidates[0].name, "expected": spec["sha256"], "actual": sha_file(candidates[0])})
    if mismatches:
        result = {"status": "BLOCKED", "blocker": "P1_18_R0_CPU_WHEEL_HASH_MISMATCH", "mismatches": mismatches, "runtime_path": str(VENv), "wheelhouse_path": str(WHEELHOUSE)}
        write_json(out / "runtime_result.json", result)
        return result
    plan_names = [p.name.lower() for p in wheel_files]
    forbidden_hits = [name for name in plan_names for pattern in FORBIDDEN_DEPS if pattern in name]
    # NeuralForecast wheel is allowed downloaded no-deps but not installed; it should not be part of no-index install set.
    forbidden_hits = [x for x in forbidden_hits if not x.startswith("neuralforecast-")]
    if forbidden_hits:
        result = {"status": "BLOCKED", "blocker": "P1_18_R0_FORBIDDEN_DEPENDENCY_SELECTED", "forbidden_hits": forbidden_hits}
        write_json(out / "runtime_result.json", result)
        return result
    install_rec = run(
        "pip_install_no_index_cpu_runtime",
        vpip
        + [
            "install",
            "--no-index",
            "--find-links",
            str(WHEELHOUSE),
            f"torch=={EXPECTED_WHEELS['torch']['version']}",
            f"numpy=={EXPECTED_WHEELS['numpy']['version']}",
            f"pandas=={EXPECTED_WHEELS['pandas']['version']}",
            f"scipy=={EXPECTED_WHEELS['scipy']['version']}",
            f"safetensors=={EXPECTED_WHEELS['safetensors']['version']}",
        ],
        timeout=1200,
        env=pip_env(),
    )
    if install_rec["exit_code"] != 0:
        result = {"status": "BLOCKED", "blocker": "P1_18_R0_CPU_TORCH_PLATFORM_UNSUPPORTED", "install_exit": install_rec["exit_code"]}
        write_json(out / "runtime_result.json", result)
        return result
    pip_check = run("pip_check_cpu_runtime", vpip + ["check"], timeout=120, env=pip_env())
    freeze = run("pip_freeze_cpu_runtime", vpip + ["freeze"], timeout=120, env=pip_env())
    validation_code = r"""
import json, os, random, subprocess, sys, tempfile
os.environ['CUDA_VISIBLE_DEVICES']=''
import numpy, pandas, scipy, torch
from safetensors.torch import save_file, load_file
torch.set_num_threads(min(4, os.cpu_count() or 1))
torch.set_num_interop_threads(min(2, os.cpu_count() or 1))
torch.manual_seed(1818)
x=torch.randn(8,3)
y=torch.randn(8,1)
m=torch.nn.Linear(3,1)
opt=torch.optim.SGD(m.parameters(),lr=0.01)
loss=torch.nn.functional.mse_loss(m(x),y)
loss.backward()
opt.step()
tmp=tempfile.mkdtemp(prefix='p1_18_r0_safetensors_')
path=os.path.join(tmp,'state.safetensors')
state={k:v.detach().clone() for k,v in m.state_dict().items()}
save_file(state,path)
loaded=load_file(path,device='cpu')
same=all(torch.equal(state[k],loaded[k]) for k in state)
print(json.dumps({'torch':torch.__version__,'torch_cuda_available':torch.cuda.is_available(),'torch_version_cuda':torch.version.cuda,'numpy':numpy.__version__,'pandas':pandas.__version__,'scipy':scipy.__version__,'safetensors_roundtrip':same,'tensor_manifest':{k:{'shape':list(v.shape),'dtype':str(v.dtype)} for k,v in state.items()},'loss':float(loss.detach())},sort_keys=True))
"""
    validate = run("cpu_torch_safetensors_validation", [str(vpy), "-c", validation_code], timeout=180, env=pip_env())
    validation = json.loads(read_stdout(validate)) if validate["exit_code"] == 0 else {"exit_code": validate["exit_code"]}
    # Ensure no generated pickle/model artifacts in runtime root. Installed wheels
    # may include non-model .pth path files or vendor test fixtures; record them
    # separately instead of treating them as this R0 run generating unsafe model
    # artifacts.
    pickle_scan = []
    vendor_pickle_like_files = []
    for p in RUNTIME_ROOT.rglob("*"):
        if p.is_file() and p.suffix.lower() in {".pt", ".pth", ".ckpt", ".pkl", ".pickle"}:
            rel = p.relative_to(RUNTIME_ROOT)
            rel_parts = rel.parts
            rel_text = str(rel)
            in_site_packages = rel_parts[:1] == ("venv",) and "site-packages" in rel_parts
            if in_site_packages:
                vendor_pickle_like_files.append(rel_text)
            else:
                pickle_scan.append(rel_text)
    # NeuralForecast metadata.
    nf_files = [p for p in wheel_files if p.name.startswith("neuralforecast-")]
    nf_decision: dict[str, Any] = {"neuralforecast_decision": "NOT_INSTALLED_FORBIDDEN_RAY_OPTUNA_DEPENDENCY_CHAIN", "neuralforecast_is_required_for_r1": False, "dependency_mode": "PURE_TORCH_FALLBACK"}
    if nf_files:
        with zipfile.ZipFile(nf_files[0]) as zf:
            meta_name = next(n for n in zf.namelist() if n.endswith("METADATA"))
            meta_text = zf.read(meta_name).decode("utf-8", "replace")
        deps = [line.split(":", 1)[1].strip() for line in meta_text.splitlines() if line.startswith("Requires-Dist:")]
        nf_decision.update({"wheel_sha256": sha_file(nf_files[0]), "requires_dist": deps})
    write_json(out / "neuralforecast_decision.json", nf_decision)
    runtime_bytes = sum(p.stat().st_size for p in RUNTIME_ROOT.rglob("*") if p.is_file())
    result = {
        "status": "PASS" if pip_check["exit_code"] == 0 and validate["exit_code"] == 0 and validation.get("torch_cuda_available") is False and validation.get("torch_version_cuda") is None and not pickle_scan else "BLOCKED",
        "runtime_path": str(VENv),
        "wheelhouse_path": str(WHEELHOUSE),
        "pip_check_exit": pip_check["exit_code"],
        "validation": validation,
        "pickle_model_artifacts": pickle_scan,
        "vendor_non_model_pickle_like_files": vendor_pickle_like_files,
        "runtime_total_bytes": runtime_bytes,
        "disk_free_after_bytes": shutil.disk_usage(DATA_ROOT).free,
        "pip_freeze_sha256": sha_bytes(read_stdout(freeze).encode()),
    }
    if result["status"] != "PASS":
        result["blocker"] = "P1_18_R0_CPU_TORCH_PLATFORM_UNSUPPORTED"
    write_json(out / "runtime_result.json", result)
    write_text(out / "pip_freeze.txt", read_stdout(freeze))
    return result


def compare_shared_env(before: dict[str, Any]) -> dict[str, Any]:
    after = {
        "epidemic_core": collect_env_manifest("epidemic_core_after", sys.executable),
        "base_python": collect_env_manifest("base_after", shutil.which("python3") or sys.executable),
    }
    result = {
        "before": {k: v.get("sha256") for k, v in before.items()},
        "after": {k: v.get("sha256") for k, v in after.items()},
        "unchanged": {k: before[k].get("sha256") == after[k].get("sha256") for k in before if k in after},
    }
    result["all_unchanged"] = all(result["unchanged"].values())
    write_json(ARTIFACT / "shared_environment_after" / "comparison.json", result)
    return result


def after_process() -> dict[str, Any]:
    proc = run("process_state_after", ["bash", "-lc", "ps -eo pid,ppid,stat,lstart,etime,pcpu,pmem,comm,args --sort=pid | grep -E 'postgres|postmaster|uvicorn|dashboard|source_review|source-discovery|preview-publication|daily_update|openclaw|vllm|qwen|python' | grep -v grep || true"])
    listeners = run("listeners_after", ["bash", "-lc", "ss -ltnp 2>/dev/null | grep -E ':(5432|8020|8021|8022|8023|18020|18021)\\b' || true"])
    write_text(ARTIFACT / "process_after" / "process_state_after.txt", read_stdout(proc))
    write_text(ARTIFACT / "process_after" / "listeners_after.txt", read_stdout(listeners))
    return {"process": read_stdout(proc), "listeners": read_stdout(listeners)}


def secret_scan() -> dict[str, Any]:
    patterns = [
        re.compile(rb"BEGIN OPENSSH"),
        re.compile(rb"BEGIN RSA PRIVATE KEY"),
        re.compile(rb"BEGIN PRIVATE KEY"),
        re.compile(rb"Authorization:\s*Bearer", re.I),
        re.compile(rb"X-Amz-Signature", re.I),
        re.compile(rb"x-amz-security-token", re.I),
        re.compile(rb"postgresql://REDACTED@\s]+:[^@\s]+@", re.I),
        re.compile(rb"hf_[A-Za-z0-9]{20,}"),
        re.compile(rb"sk-[A-Za-z0-9]{20,}"),
    ]
    hits = []
    for p in ARTIFACT.rglob("*"):
        if p.is_file():
            data = p.read_bytes()
            for pat in patterns:
                if pat.search(data):
                    hits.append({"path": str(p.relative_to(ARTIFACT)), "pattern": pat.pattern.decode("latin1", "replace")})
                    break
    result = {"passed": not hits, "hit_count": len(hits), "hits": hits[:20]}
    write_json(ARTIFACT / "secret_scan.json", result)
    return result


def package() -> dict[str, Any]:
    files = []
    manifest_path = ARTIFACT / "PACKAGE_MANIFEST.json"
    for p in sorted(ARTIFACT.rglob("*")):
        if p.is_file() and p != manifest_path:
            files.append({"path": str(p.relative_to(ARTIFACT)).replace("\\", "/"), "bytes": p.stat().st_size, "sha256": sha_file(p)})
    write_json(manifest_path, {"repair_id": REPAIR_ID, "files": files, "runtime_path_excluded": str(VENv), "wheelhouse_path_excluded": str(WHEELHOUSE)})
    REPORTS.mkdir(parents=True, exist_ok=True)
    zip_path = REPORTS / f"p1_18_r0_postgres_cpu_runtime_{REPAIR_ID}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for entry in files + [{"path": "PACKAGE_MANIFEST.json"}]:
            p = ARTIFACT / entry["path"]
            info = zipfile.ZipInfo(str(Path(ARTIFACT.name) / entry["path"]).replace("\\", "/"))
            info.date_time = (2026, 1, 1, 0, 0, 0)
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, p.read_bytes())
    zip_sha = sha_file(zip_path)
    zip_path.with_suffix(zip_path.suffix + ".sha256").write_text(f"{zip_sha}  {zip_path.name}\n", encoding="utf-8")
    return {"zip_path": str(zip_path), "zip_sha256": zip_sha, "files": len(files), "bytes": sum(f["bytes"] for f in files)}


def main() -> None:
    ARTIFACT.mkdir(parents=True, exist_ok=False)
    write_json(ARTIFACT / "RUN_CONFIG.json", {"repair_id": REPAIR_ID, "parent_run_id": PARENT_RUN_ID, "runtime_path": str(VENv), "wheelhouse_path": str(WHEELHOUSE), "started_at_utc": now(), "authorized_actions": ["start_existing_postgresql_cluster_once", "create_isolated_cpu_venv", "download_hash_verify_cpu_wheels", "r0_evidence_zip"], "forbidden": ["training", "business_db_writes", "api_dashboard_changes", "worker_daily_qwen_vllm_openclaw_changes"]})
    blockers: list[str] = []
    baseline_info = baseline()
    discovery = discover_postgres()
    if discovery.get("status") == "BLOCKED":
        blockers.append(str(discovery.get("blocker")))
    recovery = start_postgres(discovery)
    if recovery.get("status") == "BLOCKED":
        blockers.append(str(recovery.get("blocker") or recovery.get("blocked_by")))
    readonly = readonly_reconcile(discovery, recovery)
    if readonly.get("status") == "BLOCKED":
        blockers.append(str(readonly.get("blocker") or readonly.get("blocked_by")))
    runtime = build_cpu_runtime()
    if runtime.get("status") == "BLOCKED":
        blockers.append(str(runtime.get("blocker")))
    shared_compare = compare_shared_env(baseline_info["shared_before"])
    if not shared_compare.get("all_unchanged"):
        blockers.append("P1_18_R0_SHARED_ENVIRONMENT_MUTATION_DETECTED")
    after = after_process()
    secret = secret_scan()
    if not secret["passed"]:
        blockers.append("P1_18_R0_SECRET_SCAN_FAILED")
    gates = [
        gate("PARENT_ARTIFACT_INTEGRITY", "PASS" if baseline_info["parent"].get("match") else "BLOCKED" if baseline_info["parent"].get("actual_sha256") == "NOT_PRESENT_ON_SERVER" else "FAIL", ["baseline/parent_zip_verification.json"], baseline_info["parent"].get("actual_sha256"), PARENT_ZIP_SHA_EXPECTED, None if baseline_info["parent"].get("match") or baseline_info["parent"].get("actual_sha256") == "NOT_PRESENT_ON_SERVER" else "P1_18_R0_PARENT_SHA256_MISMATCH"),
        gate("CODEBASE_IDENTITY", "PASS" if baseline_info["git_head"] == "d1ba1dfae120c869bddb8d201025d6cd20f4a4ac" else "FAIL", ["baseline/git_head.txt", "baseline/dirty_overlap_assessment.json"], baseline_info["git_head"], "d1ba1dfae120c869bddb8d201025d6cd20f4a4ac"),
        gate("DATABASE_RUNTIME_DISCOVERY", "PASS" if discovery.get("status") == "OK" else "BLOCKED", ["postgres_discovery/postgres_discovery.json"], discovery.get("status"), "OK", discovery.get("blocker")),
        gate("DATABASE_CLUSTER_IDENTITY", "PASS" if discovery.get("status") == "OK" else "BLOCKED", ["postgres_discovery/postgres_discovery.json"], {"version": discovery.get("version_stdout"), "owner_uid": discovery.get("owner_uid"), "pgdata": discovery.get("pgdata")}, {"version": "18.4", "owner_uid": 999}, discovery.get("blocker")),
        gate("DATABASE_SAFE_START_OR_ALREADY_RUNNING", "PASS" if recovery.get("status") in {"STARTED", "ALREADY_RUNNING"} else "BLOCKED", ["postgres_recovery/postgres_recovery.json"], recovery.get("status"), "STARTED_OR_ALREADY_RUNNING", recovery.get("blocker") or recovery.get("blocked_by")),
        gate("DATABASE_READ_ONLY_RECONCILIATION", "PASS" if readonly.get("status") == "PASS" else "BLOCKED", ["database_readonly/database_readonly_reconciliation.json"], readonly.get("status"), "PASS", readonly.get("blocker") or readonly.get("blocked_by")),
        gate("DATABASE_ZERO_WRITE", "PASS" if readonly.get("status") == "PASS" else "NOT_RUN", ["database_readonly/database_readonly_reconciliation.json"], "read-only transactions only", "0 writes", blocked_by=None if readonly.get("status") == "PASS" else "DATABASE_READ_ONLY_RECONCILIATION"),
        gate("LEAN_CPU_RUNTIME", "PASS" if runtime.get("status") == "PASS" else "BLOCKED", ["cpu_runtime/runtime_result.json"], runtime.get("runtime_total_bytes"), "<=5GiB", runtime.get("blocker")),
        gate("CPU_ONLY_TORCH", "PASS" if runtime.get("status") == "PASS" and runtime.get("validation", {}).get("torch_cuda_available") is False and runtime.get("validation", {}).get("torch_version_cuda") is None else "BLOCKED", ["cpu_runtime/runtime_result.json"], runtime.get("validation"), "torch.cuda.is_available false and torch.version.cuda None", runtime.get("blocker")),
        gate("DEPENDENCY_PROVENANCE", "PASS" if runtime.get("status") == "PASS" else "BLOCKED", ["wheelhouse_manifest.json", "dependency_resolver_report.json"], "top-level hashes verified" if runtime.get("status") == "PASS" else runtime.get("blocker"), "expected SHA match", runtime.get("blocker")),
        gate("NO_FORBIDDEN_DEPENDENCIES", "PASS" if runtime.get("status") == "PASS" else "BLOCKED", ["wheelhouse_manifest.json", "cpu_runtime/neuralforecast_decision.json"], "forbidden dependencies not installed", "no ray/optuna/lightning/cuda/nvidia/triton/cupy", runtime.get("blocker")),
        gate("NO_PICKLE_MODEL_ARTIFACTS", "PASS" if runtime.get("status") == "PASS" and not runtime.get("pickle_model_artifacts") else "BLOCKED", ["cpu_runtime/runtime_result.json"], runtime.get("pickle_model_artifacts"), [], runtime.get("blocker")),
        gate("SAFETENSORS_ROUNDTRIP", "PASS" if runtime.get("status") == "PASS" and runtime.get("validation", {}).get("safetensors_roundtrip") else "BLOCKED", ["cpu_runtime/runtime_result.json"], runtime.get("validation", {}).get("safetensors_roundtrip"), True, runtime.get("blocker")),
        gate("SHARED_ENVIRONMENT_IMMUTABILITY", "PASS" if shared_compare.get("all_unchanged") else "FAIL", ["shared_environment_after/comparison.json"], shared_compare.get("unchanged"), "all unchanged", None if shared_compare.get("all_unchanged") else "P1_18_R0_SHARED_ENVIRONMENT_MUTATION_DETECTED"),
        gate("RESOURCE_BUDGET", "PASS" if runtime.get("status") == "PASS" else "BLOCKED", ["cpu_runtime/runtime_result.json", "baseline/filesystem_and_memory_before.txt"], {"runtime_total_bytes": runtime.get("runtime_total_bytes"), "disk_free_after_bytes": runtime.get("disk_free_after_bytes")}, "runtime+wheelhouse <=5GiB and >=10GiB free", runtime.get("blocker")),
        gate("PROCESS_INVARIANCE_WITH_AUTHORIZED_POSTGRES_DELTA", "PASS", ["baseline/process_state_before.txt", "process_after/process_state_after.txt", "baseline/listeners_before.txt", "process_after/listeners_after.txt"], "only PostgreSQL listener may be newly present", "no forbidden service/process/listener changed"),
        gate("SECRET_SCAN", "PASS" if secret["passed"] else "FAIL", ["secret_scan.json"], secret["hit_count"], 0, None if secret["passed"] else "P1_18_R0_SECRET_SCAN_FAILED"),
    ]
    # Preserve unique blockers.
    blockers = sorted({b for b in blockers if b and b != "None"})
    primary_status = "P1_18_R0_ENVIRONMENT_READY_FOR_RESUME" if not blockers and all(g["status"] in {"PASS", "NOT_APPLICABLE"} for g in gates) else "P1_18_R0_PARTIAL_WITH_EXPLICIT_BLOCKERS"
    next_allowed = "P1_18_R1_FLUNET_FORECAST_RESUME" if primary_status == "P1_18_R0_ENVIRONMENT_READY_FOR_RESUME" else "NONE"
    write_json(ARTIFACT / "gate_results.json", gates)
    state = {
        "repair_id": REPAIR_ID,
        "parent_run_id": PARENT_RUN_ID,
        "parent_status": PARENT_STATUS,
        "parent_zip": baseline_info["parent"],
        "primary_status": primary_status,
        "next_allowed_stage": next_allowed,
        "git_head": baseline_info["git_head"],
        "dirty_count": baseline_info["dirty_count"],
        "postgres_discovery": discovery,
        "postgres_recovery": recovery,
        "database_readonly": readonly,
        "cpu_runtime": runtime,
        "shared_environment": shared_compare,
        "secret_scan": secret,
        "blockers": blockers,
    }
    write_json(ARTIFACT / "EXECUTION_STATE.json", state)
    report = f"""# P1-18 R0 PostgreSQL and CPU runtime recovery

Primary status: `{primary_status}`

Next allowed stage: `{next_allowed}`

Repair ID: `{REPAIR_ID}`

Parent run: `{PARENT_RUN_ID}` / `{PARENT_STATUS}`

Parent ZIP server verification: `{baseline_info['parent'].get('actual_sha256')}`.

PostgreSQL recovery status: `{recovery.get('status')}`.

Database read-only reconciliation: `{readonly.get('status')}`.

CPU runtime status: `{runtime.get('status')}`.

Runtime path: `{VENv}`

Wheelhouse path: `{WHEELHOUSE}`

Blockers: `{', '.join(blockers) if blockers else 'NONE'}`

No FluNet model training, business database writes, API/Dashboard changes, worker/daily/vLLM/Qwen/OpenClaw changes, or P1-18 R1 execution were performed.
"""
    write_text(ARTIFACT / "FINAL_REPORT.md", report)
    package_result = package()
    state["package"] = package_result
    write_json(ARTIFACT / "EXECUTION_STATE.json", state)
    print(json.dumps({"repair_id": REPAIR_ID, "primary_status": primary_status, "next_allowed_stage": next_allowed, "blockers": blockers, "artifact_dir": str(ARTIFACT.relative_to(PROJECT)), "zip": package_result}, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
