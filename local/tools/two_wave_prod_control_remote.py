from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


ROOT_EXECUTION_ID = os.environ.get(
    "ROOT_EXECUTION_ID",
    "three_disease_two_wave_"
    + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    + "_"
    + secrets.token_hex(4),
)
WAVE1_EXECUTION_ID = f"{ROOT_EXECUTION_ID}_wave1_hanta_legionella"
WAVE2_EXECUTION_ID = f"{ROOT_EXECUTION_ID}_wave2_nipah"
FINAL_PACKAGE_ID = f"{ROOT_EXECUTION_ID}_final_merged"

PROJECT = Path(os.environ.get("PROJECT", "USER_HOME/autodl-tmp/epidemic-openclaw")).resolve()
EVIDENCE_ROOT = Path(os.environ.get("EVIDENCE_ROOT", str(PROJECT.parent / "epidemic-openclaw-evidence"))).resolve()
RUN_ROOT = EVIDENCE_ROOT / ROOT_EXECUTION_ID
M0_DIR = RUN_ROOT / "m0"
LOG_DIR = RUN_ROOT / "logs"
ARCHIVE_DIR = RUN_ROOT / "archives"
WAVE1_DIR = RUN_ROOT / "wave1"
WAVE2_DIR = RUN_ROOT / "wave2"
FINAL_DIR = RUN_ROOT / "final_state"
FINAL_REPORTS_DIR = RUN_ROOT / "final_reports"
BACKUP_ROOT = Path(os.environ.get("BACKUP_ROOT", str(PROJECT.parent / "epidemic-openclaw-backups"))).resolve()
BACKUP_DIR = BACKUP_ROOT / ROOT_EXECUTION_ID / "wave1"
COMMAND_INDEX = RUN_ROOT / "COMMAND_EXECUTION_INDEX.jsonl"

KNOWN_PRIOR_PACKAGE_SHA = "0e792784eba84eb71c4d1424d25130f63b5e3fa21ded2d2222c0d41232da7948"
PROTECTED_RUN_IDS = [
    "c1ce1947-1ab4-5f2d-b69e-a4e9c4668825",
    "2adad614-7f25-5e58-ba00-167ca5f87606",
]
SENSITIVE_RE = re.compile(
    r"(?i)(password|token|secret|api[_-]?key|authorization|cookie)(\s*[=:]\s*)([^,\s\"']+)"
)
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


def db_url() -> str:
    raw = os.environ.get("MIGRATION_DATABASE_URL") or os.environ.get("DATABASE_URL") or os.environ.get("APP_DATABASE_URL")
    if not raw:
        raise RuntimeError("DATABASE_URL_UNAVAILABLE")
    url = make_url(raw)
    password = os.environ.get("POSTGRES_ADMIN_PASSWORD")
    if password:
        url = url.set(username=os.environ.get("POSTGRES_ADMIN_USER") or url.username or "epidemicdb", password=password)
    return url.render_as_string(hide_password=False)


def public_db_identity(url_string: str) -> dict[str, Any]:
    url = make_url(url_string)
    return {
        "drivername": url.drivername,
        "host_present": bool(url.host),
        "port": url.port,
        "database": url.database,
        "username_present": bool(url.username),
        "password_present": bool(url.password),
    }


def engine():
    return create_engine(db_url(), future=True, pool_pre_ping=True, hide_parameters=True)


def rows(conn: Any, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(text(sql), params or {}).mappings().all()]


def scalar(conn: Any, sql: str, params: dict[str, Any] | None = None) -> Any:
    return conn.execute(text(sql), params or {}).scalar_one()


def scalar_none(conn: Any, sql: str, params: dict[str, Any] | None = None) -> Any:
    return conn.execute(text(sql), params or {}).scalar_one_or_none()


def read_only_tx_check() -> dict[str, Any]:
    check_engine = create_engine(db_url(), future=True, pool_pre_ping=True, hide_parameters=True, isolation_level="AUTOCOMMIT")
    try:
        with check_engine.connect() as conn:
            conn.exec_driver_sql("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            conn.exec_driver_sql("SET LOCAL statement_timeout = '30s'")
            conn.exec_driver_sql("SET LOCAL idle_in_transaction_session_timeout = '60s'")
            value = conn.exec_driver_sql("SHOW transaction_read_only").scalar_one()
            conn.exec_driver_sql("ROLLBACK")
            return {"state": "PASS", "transaction_read_only": value}
    except Exception as exc:
        return {"state": "FAILED", "reason": type(exc).__name__, "message": str(exc)}
    finally:
        check_engine.dispose()


def run_command(
    command_id: str,
    args: list[str],
    *,
    cwd: Path = PROJECT,
    timeout: int = 300,
    env_extra: dict[str, str] | None = None,
    stage: str = "M0",
    allow_failure: bool = True,
) -> dict[str, Any]:
    start = now()
    t0 = time.time()
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run(args, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=timeout)
    end = now()
    stdout_path = LOG_DIR / f"{command_id}.stdout.txt"
    stderr_path = LOG_DIR / f"{command_id}.stderr.txt"
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stdout_path.write_text(redact(proc.stdout), encoding="utf-8")
    stderr_path.write_text(redact(proc.stderr), encoding="utf-8")
    receipt = {
        "command_id": command_id,
        "execution_id": ROOT_EXECUTION_ID,
        "wave": None,
        "stage": stage,
        "cwd": str(cwd),
        "identity": "remote_project_user",
        "start_utc": start,
        "end_utc": end,
        "duration_seconds": round(time.time() - t0, 3),
        "command_redacted": [redact(x) for x in args],
        "exit_code": proc.returncode,
        "state": "PASS" if proc.returncode == 0 else ("FAILED" if not allow_failure else "BLOCKED"),
        "reason": None if proc.returncode == 0 else f"EXIT_{proc.returncode}",
        "timeout_seconds": timeout,
        "stdout_path": str(stdout_path.relative_to(RUN_ROOT)),
        "stderr_path": str(stderr_path.relative_to(RUN_ROOT)),
        "stdout_size": stdout_path.stat().st_size,
        "stderr_size": stderr_path.stat().st_size,
        "stdout_sha256": sha256_bytes(stdout_path.read_bytes()),
        "stderr_sha256": sha256_bytes(stderr_path.read_bytes()),
    }
    append_jsonl(COMMAND_INDEX, receipt)
    if proc.returncode and not allow_failure:
        raise RuntimeError(f"{command_id}_FAILED")
    return receipt


def git_info() -> dict[str, Any]:
    root = run_command("git_root", ["git", "rev-parse", "--show-toplevel"], allow_failure=False)
    head = run_command("git_head", ["git", "rev-parse", "HEAD"], allow_failure=False)
    branch = run_command("git_branch", ["git", "branch", "--show-current"], allow_failure=True)
    status_proc = subprocess.run(["git", "status", "--porcelain=v2", "-z"], cwd=str(PROJECT), capture_output=True)
    raw_path = LOG_DIR / "git_status_porcelain_v2_z.raw"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(status_proc.stdout)
    entries = []
    for item in status_proc.stdout.split(b"\0"):
        if not item:
            continue
        text_item = item.decode("utf-8", errors="replace")
        parts = text_item.split(" ")
        path_text = parts[-1] if parts else text_item
        if "\t" in text_item:
            path_text = text_item.split("\t")[-1]
        full = (PROJECT / path_text).resolve() if not Path(path_text).is_absolute() else Path(path_text)
        entry: dict[str, Any] = {
            "raw": text_item,
            "path": path_text,
            "status_class": text_item[:2],
            "exists": full.exists() or full.is_symlink(),
            "deleted": not full.exists() and not full.is_symlink(),
        }
        try:
            st = os.lstat(full)
            entry.update({"mode": oct(stat.S_IMODE(st.st_mode)), "size": st.st_size})
            if stat.S_ISLNK(st.st_mode):
                entry["file_type"] = "symlink"
                entry["symlink_target"] = os.readlink(full)
            elif stat.S_ISREG(st.st_mode):
                entry["file_type"] = "file"
                entry["sha256"] = sha256_bytes(full.read_bytes())
            elif stat.S_ISDIR(st.st_mode):
                entry["file_type"] = "directory"
            else:
                entry["file_type"] = "other"
        except Exception as exc:
            entry["stat_error"] = type(exc).__name__
        entries.append(entry)
    status_text = status_proc.stdout.decode("utf-8", errors="replace")
    classified = {
        "staged": sum(1 for e in entries if e["raw"].startswith("1 ") and len(e["raw"]) > 3 and e["raw"].split(" ")[1][0] != "."),
        "unstaged": sum(1 for e in entries if e["raw"].startswith("1 ") and len(e["raw"]) > 3 and e["raw"].split(" ")[1][1] != "."),
        "untracked": sum(1 for e in entries if e["raw"].startswith("? ")),
        "deleted": sum(1 for e in entries if e.get("deleted")),
        "renamed": sum(1 for e in entries if e["raw"].startswith("2 ")),
        "symlink": sum(1 for e in entries if e.get("file_type") == "symlink"),
    }
    payload = {
        "pwd_P": str(PROJECT.resolve()),
        "git_root_stdout_log": root["stdout_path"],
        "git_head_stdout_log": head["stdout_path"],
        "git_branch_stdout_log": branch["stdout_path"],
        "git_root": (LOG_DIR / root["stdout_path"]).read_text(encoding="utf-8").strip() if (LOG_DIR / root["stdout_path"]).exists() else None,
        "head_sha": (LOG_DIR / head["stdout_path"]).read_text(encoding="utf-8").strip() if (LOG_DIR / head["stdout_path"]).exists() else None,
        "branch": (LOG_DIR / branch["stdout_path"]).read_text(encoding="utf-8").strip() if (LOG_DIR / branch["stdout_path"]).exists() else None,
        "status_exit_code": status_proc.returncode,
        "status_raw_path": str(raw_path.relative_to(RUN_ROOT)),
        "status_raw_sha256": sha256_bytes(status_proc.stdout),
        "status_raw_base64_sha256": sha256_bytes(base64.b64encode(status_proc.stdout)),
        "dirty_total": len(entries),
        "classification_counts": classified,
        "entries": entries,
    }
    write_json(M0_DIR / "PREEXISTING_DIRTY_INVENTORY.json", payload)
    return payload


def db_fingerprint(conn: Any) -> dict[str, Any]:
    url = db_url()
    tables = [
        "epidemic.normalized_observations",
        "epidemic.quality_assessments",
        "epidemic.raw_artifacts",
        "epidemic.source_fetches",
        "epidemic.dataset_snapshots",
        "epidemic.dataset_snapshot_observations",
        "epidemic.forecast_runs",
        "epidemic.forecast_values",
        "epidemic_p1_12.trusted_observation_lineage",
        "epidemic_p1_12.trusted_source_contracts",
    ]
    counts: dict[str, Any] = {}
    for table_name in tables:
        try:
            counts[table_name] = int(scalar(conn, f"SELECT count(*) FROM {table_name}"))
        except Exception as exc:
            try:
                conn.rollback()
            except Exception:
                pass
            counts[table_name] = {"state": "UNVERIFIED", "reason": type(exc).__name__}
    payload = {
        "observed_at_utc": now(),
        "db_identity_redacted": public_db_identity(url),
        "current_database": scalar(conn, "SELECT current_database()"),
        "current_user": scalar(conn, "SELECT current_user"),
        "inet_server_addr_present": bool(scalar_none(conn, "SELECT inet_server_addr()::text")),
        "inet_server_port": scalar_none(conn, "SELECT inet_server_port()"),
        "system_identifier": scalar_none(conn, "SELECT system_identifier::text FROM pg_control_system()"),
        "transaction_read_only_check": read_only_tx_check(),
        "alembic_versions": rows(conn, "SELECT version_num FROM epidemic.alembic_version ORDER BY version_num"),
        "counts": counts,
        "three_disease_catalog": rows(
            conn,
            """
            SELECT disease_slug,source_key,country_alpha3,count(*)::int rows,min(period_start)::text date_min,max(period_end)::text date_max
            FROM epidemic.dashboard_history_series_v2
            WHERE disease_slug IN ('hantavirus','legionella','nipah')
            GROUP BY disease_slug,source_key,country_alpha3
            ORDER BY disease_slug,source_key,country_alpha3
            """,
        ),
    }
    try:
        if scalar_none(conn, "SELECT to_regclass('epidemic.latest_published_forecasts_v1')::text"):
            payload["active_forecast_pointers"] = rows(
                conn,
                """
                SELECT * FROM epidemic.latest_published_forecasts_v1
                WHERE disease_slug IN ('covid-19','influenza','hantavirus','legionella','nipah')
                ORDER BY disease_slug,source_key NULLS LAST,country_iso3 NULLS LAST,run_id
                """,
            )
        else:
            payload["active_forecast_pointers"] = []
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        payload["active_forecast_pointers"] = {"state": "UNVERIFIED", "reason": type(exc).__name__, "message": str(exc)}
    return payload


def protected_runs(conn: Any) -> dict[str, Any]:
    run_rows = rows(
        conn,
        """
        SELECT *
        FROM epidemic.forecast_runs
        WHERE id::text = ANY(CAST(:ids AS text[]))
        ORDER BY id::text
        """,
        {"ids": PROTECTED_RUN_IDS},
    )
    refs: dict[str, Any] = {}
    related_tables = [
        "epidemic.forecast_values",
        "epidemic.forecast_artifacts",
        "epidemic.forecast_fold_metrics",
        "epidemic.forecast_history_points",
        "epidemic.forecast_run_invalidations",
    ]
    for table_name in related_tables:
        try:
            refs[table_name] = rows(
                conn,
                f"SELECT * FROM {table_name} WHERE forecast_run_id::text = ANY(CAST(:ids AS text[])) ORDER BY forecast_run_id::text,id::text",
                {"ids": PROTECTED_RUN_IDS},
            )
        except Exception as exc:
            try:
                conn.rollback()
            except Exception:
                pass
            refs[table_name] = {"state": "UNVERIFIED", "reason": type(exc).__name__}
    payload = {"runs": run_rows, "related": refs}
    payload["fingerprint_sha256"] = sha_obj(payload)
    return payload


def http_probe() -> list[dict[str, Any]]:
    import urllib.error
    import urllib.request

    targets = [
        "http://127.0.0.1:8020/healthz",
        "http://127.0.0.1:8020/readyz",
        "http://127.0.0.1:8020/api/v2/catalog/options",
        "http://127.0.0.1:8020/api/v2/history/metadata",
        "http://127.0.0.1:8020/api/v2/history/series",
        "http://127.0.0.1:8020/api/v1/fasttrack-forecast/runs",
    ]
    out = []
    for url in targets:
        start = time.time()
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "two-wave-m0/1.0"}), timeout=15) as resp:
                body = resp.read()
                out.append(
                    {
                        "url_redacted": url,
                        "status": getattr(resp, "status", 200),
                        "content_type": resp.headers.get("content-type"),
                        "bytes": len(body),
                        "sha256": sha256_bytes(body),
                        "elapsed_ms": int((time.time() - start) * 1000),
                        "body_path": str((LOG_DIR / f"http_{len(out)}.body.txt").relative_to(RUN_ROOT)),
                    }
                )
                (LOG_DIR / f"http_{len(out)-1}.body.txt").write_text(redact(body.decode("utf-8", errors="replace")), encoding="utf-8")
        except urllib.error.HTTPError as exc:
            body = exc.read()
            body_path = LOG_DIR / f"http_{len(out)}.body.txt"
            body_path.write_text(redact(body.decode("utf-8", errors="replace")), encoding="utf-8")
            out.append(
                {
                    "url_redacted": url,
                    "status": exc.code,
                    "content_type": exc.headers.get("content-type") if exc.headers else None,
                    "bytes": len(body),
                    "sha256": sha256_bytes(body),
                    "elapsed_ms": int((time.time() - start) * 1000),
                    "body_path": str(body_path.relative_to(RUN_ROOT)),
                }
            )
        except Exception as exc:
            out.append({"url_redacted": url, "status": 0, "error_type": type(exc).__name__, "error": str(exc)})
    return out


def service_state(dirty: dict[str, Any]) -> dict[str, Any]:
    process_receipt = run_command(
        "process_inventory",
        [
            sys.executable,
            "-c",
            (
                "import pathlib,json,os;"
                "items=[];"
                "root=str(pathlib.Path.cwd());"
                "\nfor p in pathlib.Path('/proc').iterdir():\n"
                "    if not p.name.isdigit(): continue\n"
                "    try:\n"
                "        cmd=(p/'cmdline').read_bytes().replace(b'\\x00',b' ').decode('utf-8','replace')\n"
                "        cwd=str((p/'cwd').resolve())\n"
                "        ppid=(p/'status').read_text(errors='ignore').split('PPid:')[1].split('\\n')[0].strip()\n"
                "    except Exception: continue\n"
                "    if 'epidemic-openclaw' in cmd or 'epidemic-openclaw' in cwd:\n"
                "        items.append({'pid':int(p.name),'ppid':ppid,'cwd':cwd,'cmdline':cmd})\n"
                "print(json.dumps(items,ensure_ascii=False))"
            ),
        ],
        timeout=120,
    )
    listen_receipt = run_command("listening_sockets", ["ss", "-ltnp"], timeout=120)
    ps_data = []
    try:
        ps_data = json.loads((LOG_DIR / process_receipt["stdout_path"]).read_text(encoding="utf-8") or "[]")
    except Exception:
        pass
    source_routes: list[dict[str, Any]] = []
    app_py = PROJECT / "src" / "epidemic_core" / "dashboard" / "app.py"
    if app_py.exists():
        for line in app_py.read_text(encoding="utf-8", errors="replace").splitlines():
            match = re.search(r"@app\.(get|post|put|delete)\(\"([^\"]+)\"", line)
            if match:
                source_routes.append({"method": match.group(1).upper(), "path": match.group(2)})
    runtime_dirty_paths = [
        e["path"]
        for e in dirty.get("entries", [])
        if e.get("path", "").startswith(("src/", "scripts/", "migrations/", "alembic", "pyproject.toml"))
    ]
    service_cwd_in_project = any(str(PROJECT) in str(p.get("cwd", "")) for p in ps_data)
    reproducibility_state = (
        "BLOCKED_NONREPRODUCIBLE_DIRTY_RUNTIME"
        if service_cwd_in_project and runtime_dirty_paths
        else "PASS"
    )
    payload = {
        "observed_at_utc": now(),
        "process_inventory_log": process_receipt,
        "listening_sockets_log": listen_receipt,
        "processes_sanitized": [
            {**p, "cmdline": redact(p.get("cmdline", ""))}
            for p in ps_data
        ],
        "source_routes": source_routes,
        "http_probe": http_probe(),
        "runtime_dirty_paths": runtime_dirty_paths,
        "service_cwd_in_project": service_cwd_in_project,
        "service_runtime_reproducibility_state": reproducibility_state,
    }
    return payload


def prior_package_fingerprint() -> dict[str, Any]:
    candidates = [
        PROJECT / "reports" / "three_disease_repair_20260727T090216Z_9d79bc70.zip",
        PROJECT / "reports" / "three_disease_20260727T043925Z_3e1bbfaf.zip",
    ]
    out = []
    for path in candidates:
        if path.exists():
            st = os.lstat(path)
            value = sha256_bytes(path.read_bytes())
            out.append(
                {
                    "path": str(path),
                    "size": st.st_size,
                    "mode": oct(stat.S_IMODE(st.st_mode)),
                    "sha256": value,
                    "matches_known_previous_repair_sha": value == KNOWN_PRIOR_PACKAGE_SHA,
                }
            )
        else:
            out.append({"path": str(path), "state": "UNVERIFIED", "reason": "PRIOR_PACKAGE_UNAVAILABLE"})
    return {"packages": out}


def pg_env(url_string: str) -> tuple[list[str], dict[str, str], dict[str, Any]]:
    url = make_url(url_string)
    host = url.host or "127.0.0.1"
    port = str(url.port or 5432)
    database = url.database or ""
    username = url.username or ""
    password = url.password or ""
    env = {"PGPASSWORD": password} if password else {}
    args_base = ["-h", host, "-p", port, "-U", username]
    return args_base, env, {"host_present": bool(host), "port": port, "database": database, "username_present": bool(username)}


def find_pg_tool(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    candidates = [
        Path("/usr/bin") / name,
        Path("/usr/local/bin") / name,
        PROJECT.parent / "postgresql" / "bin" / name,
        PROJECT.parent / "postgres" / "bin" / name,
        PROJECT / "storage" / "postgresql" / "bin" / name,
    ]
    candidates.extend(sorted(Path("/usr/lib/postgresql").glob(f"*/bin/{name}")) if Path("/usr/lib/postgresql").exists() else [])
    for path in candidates:
        if path.exists() and os.access(path, os.X_OK):
            return str(path)
    return None


def backup_and_restore(conn: Any) -> dict[str, Any]:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(BACKUP_DIR, 0o700)
    url = db_url()
    args_base, env, public = pg_env(url)
    dump = BACKUP_DIR / "pre_wave1_online_consistent.dump"
    restore_list = BACKUP_DIR / "pre_wave1_pg_restore_list.txt"
    pg_dump = find_pg_tool("pg_dump")
    pg_restore = find_pg_tool("pg_restore")
    if not pg_dump or not pg_restore:
        receipt = {"state": "BLOCKED", "reason": "PG_DUMP_OR_RESTORE_BINARY_MISSING", "pg_dump": bool(pg_dump), "pg_restore": bool(pg_restore)}
        write_json(M0_DIR / "DATABASE_BACKUP_RECEIPT_WAVE1.json", receipt)
        return receipt
    prod_db = public["database"]
    dump_receipt = run_command(
        "pg_dump_wave1",
        [pg_dump, *args_base, "-Fc", "-f", str(dump), prod_db],
        timeout=3600,
        env_extra=env,
        stage="M0_BACKUP",
    )
    if dump_receipt["exit_code"] != 0 or not dump.exists():
        receipt = {"state": "BLOCKED", "reason": "BLOCKED_DATABASE_BACKUP_FAILED", "dump_command": dump_receipt}
        write_json(M0_DIR / "DATABASE_BACKUP_RECEIPT_WAVE1.json", receipt)
        return receipt
    os.chmod(dump, 0o600)
    list_receipt = run_command(
        "pg_restore_list_wave1",
        [pg_restore, "--list", str(dump)],
        timeout=900,
        env_extra=env,
        stage="M0_BACKUP",
    )
    (restore_list).write_text((LOG_DIR / list_receipt["stdout_path"]).read_text(encoding="utf-8"), encoding="utf-8")
    iso_db = f"twowave_iso_{ROOT_EXECUTION_ID.replace('-', '_')}"
    if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]{0,62}", iso_db):
        raise RuntimeError("BAD_ISOLATION_DATABASE_NAME")
    admin_engine = create_engine(url, future=True, hide_parameters=True, isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as admin_conn:
            exists = scalar(admin_conn, "SELECT count(*) FROM pg_database WHERE datname=:db", {"db": iso_db})
            if exists:
                receipt = {"state": "BLOCKED", "reason": "BLOCKED_ISOLATION_RESOURCE_COLLISION", "isolation_database": iso_db}
                write_json(M0_DIR / "DATABASE_BACKUP_RECEIPT_WAVE1.json", receipt)
                return receipt
            # Creating an isolated restore database is explicitly part of the M0 gate.
            admin_conn.exec_driver_sql(f'CREATE DATABASE "{iso_db}" TEMPLATE template0')
    finally:
        admin_engine.dispose()
    restore_receipt = run_command(
        "pg_restore_wave1_isolation",
        [pg_restore, *args_base, "-d", iso_db, str(dump)],
        timeout=3600,
        env_extra=env,
        stage="M0_BACKUP",
    )
    iso_url = make_url(url).set(database=iso_db)
    iso_engine = create_engine(iso_url.render_as_string(hide_password=False), future=True, hide_parameters=True)
    try:
        with iso_engine.connect() as iso_conn:
            iso_identity = {
                "current_database": scalar(iso_conn, "SELECT current_database()"),
                "current_user": scalar(iso_conn, "SELECT current_user"),
                "system_identifier": scalar_none(iso_conn, "SELECT system_identifier::text FROM pg_control_system()"),
                "alembic_versions": rows(iso_conn, "SELECT version_num FROM epidemic.alembic_version ORDER BY version_num"),
            }
    finally:
        iso_engine.dispose()
    receipt = {
        "state": "PASS" if restore_receipt["exit_code"] == 0 and iso_identity["current_database"] != prod_db else "BLOCKED",
        "reason": None if restore_receipt["exit_code"] == 0 and iso_identity["current_database"] != prod_db else "BLOCKED_DATABASE_BACKUP_UNVERIFIED",
        "production_database": prod_db,
        "isolation_database": iso_db,
        "dump_path": str(dump),
        "dump_packaged": False,
        "dump_size": dump.stat().st_size,
        "dump_mode": oct(stat.S_IMODE(os.lstat(dump).st_mode)),
        "dump_sha256": sha256_bytes(dump.read_bytes()),
        "pg_restore_list_path": str(restore_list),
        "pg_restore_list_size": restore_list.stat().st_size,
        "pg_restore_list_sha256": sha256_bytes(restore_list.read_bytes()),
        "dump_command": dump_receipt,
        "restore_list_command": list_receipt,
        "restore_command": restore_receipt,
        "production_identity": public,
        "isolation_identity": iso_identity,
        "isolation_role_write_separation_state": "BLOCKED",
        "isolation_role_write_separation_reason": "ISOLATION_RESTORED_WITH_ADMIN_CONNECTION_NO_MIN_PRIVILEGE_BRANCH_ROLE_CREATED",
    }
    write_json(M0_DIR / "DATABASE_BACKUP_RECEIPT_WAVE1.json", receipt)
    return receipt


def readiness_root_cause(service: dict[str, Any], conn: Any) -> dict[str, Any]:
    ready = next((x for x in service.get("http_probe", []) if x.get("url_redacted", "").endswith("/readyz")), None)
    code_heads = run_command("alembic_heads", ["bash", "-lc", "if [ -x ./scripts/database/alembic ]; then ./scripts/database/alembic heads; elif command -v alembic >/dev/null 2>&1; then alembic heads; else echo ALEMBIC_CLI_UNAVAILABLE; exit 3; fi"], timeout=300)
    current = rows(conn, "SELECT version_num FROM epidemic.alembic_version ORDER BY version_num")
    root_cause = {
        "ready_http": ready,
        "database_current_revision": current,
        "code_heads_command": code_heads,
        "state": "BLOCKED" if ready and ready.get("status") != 200 else "PASS",
        "reason": None if ready and ready.get("status") == 200 else "READYZ_NOT_200_REQUIRES_CANDIDATE_FIX",
    }
    write_json(M0_DIR / "DASHBOARD_READINESS_ROOT_CAUSE.md.json", root_cause)
    write_text(
        M0_DIR / "DASHBOARD_READINESS_ROOT_CAUSE.md",
        "# Dashboard readiness root cause (M0 reproduction)\n\n"
        + canonical(root_cause)
        + "\n",
    )
    return root_cause


def status_ledger(blockers: list[str]) -> dict[str, Any]:
    diseases = ["hantavirus", "legionella", "nipah"]
    entries = []
    for disease in diseases:
        wave = "wave1" if disease in {"hantavirus", "legionella"} else "wave2"
        entries.append(
            {
                "disease": disease,
                "wave": wave,
                "production_generation": None,
                "observed_at_utc": now(),
                "official_source": {"state": "NOT_RUN", "reason": "M0_ONLY"},
                "trusted_ingestion": {"state": "NOT_RUN", "reason": "M0_ONLY"},
                "formal_api": {"state": "NOT_RUN", "reason": "M0_ONLY"},
                "triptych_browser": {"state": "NOT_RUN", "reason": "M0_ONLY"},
                "compound_data_dashboard": {"state": "NOT_RUN", "reason": "M0_ONLY"},
                "forecast_eligibility": {"state": "NOT_RUN", "reason": "M0_ONLY"},
                "formal_forecast": {"state": "NOT_RUN", "reason": "M0_ONLY"},
                "forecast_page": {"state": "NOT_RUN", "reason": "M0_ONLY"},
                "explicit_run_id": None,
                "counted": False,
                "exact_blocker": blockers[0] if blockers else None,
                "evidence": [{"path": "m0/BASELINE_DEFECT_REPRODUCTION.json"}],
            }
        )
    payload = {
        "root_execution_id": ROOT_EXECUTION_ID,
        "wave1_execution_id": WAVE1_EXECUTION_ID,
        "wave2_execution_id": WAVE2_EXECUTION_ID,
        "final_package_id": FINAL_PACKAGE_ID,
        "entries": entries,
    }
    write_json(M0_DIR / "STATUS_LEDGER.json", payload)
    return payload


def secret_scan(paths: list[Path]) -> dict[str, Any]:
    hits = []
    for path in paths:
        if not path.is_file():
            continue
        if path.suffix in {".dump"}:
            continue
        data = path.read_bytes()
        text_value = data.decode("utf-8", errors="ignore")
        for match in SENSITIVE_RE.finditer(text_value):
            hits.append({"path": str(path.relative_to(RUN_ROOT)), "pattern": "secret_keyword", "span": [match.start(), match.end()]})
        for match in DSN_RE.finditer(text_value):
            hits.append({"path": str(path.relative_to(RUN_ROOT)), "pattern": "dsn", "span": [match.start(), match.end()]})
    return {"files_scanned": len([p for p in paths if p.is_file() and p.suffix != ".dump"]), "scan_skipped_count": 0, "scan_error_count": 0, "secret_hit_count": len(hits), "hits": hits}


def package_m0() -> dict[str, Any]:
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    files = [p for p in RUN_ROOT.rglob("*") if p.is_file() and ".dump" not in p.name and p.parent != ARCHIVE_DIR]
    pre_scan = secret_scan(files)
    write_json(M0_DIR / "SECRET_SCAN_PREBUILD_REPORT.json", pre_scan)
    files = [p for p in RUN_ROOT.rglob("*") if p.is_file() and ".dump" not in p.name and p.parent != ARCHIVE_DIR and p.name != "PACKAGE_MANIFEST.json"]
    manifest = []
    for path in sorted(files):
        rel = path.relative_to(RUN_ROOT).as_posix()
        st = os.lstat(path)
        manifest.append(
            {
                "path": rel,
                "type": "file",
                "size": st.st_size,
                "mode": oct(stat.S_IMODE(st.st_mode)),
                "sha256": sha256_bytes(path.read_bytes()),
                "source_wave": "m0",
                "generation_stage": "M0",
                "required": rel.startswith("m0/") or rel == "COMMAND_EXECUTION_INDEX.jsonl",
                "sensitivity": "INTERNAL_NONSECRET",
                "producer": "two_wave_prod_control_remote.py",
                "category": "m0_evidence",
            }
        )
    write_json(M0_DIR / "PACKAGE_MANIFEST.json", {"manifest_self_excluded": True, "entries": manifest})
    manifest_path = M0_DIR / "PACKAGE_MANIFEST.json"
    zip_path = ARCHIVE_DIR / f"{ROOT_EXECUTION_ID}_m0_blocker.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for entry in manifest:
            zf.write(RUN_ROOT / entry["path"], arcname=entry["path"])
        zf.write(manifest_path, arcname=manifest_path.relative_to(RUN_ROOT).as_posix())
    sha_path = zip_path.with_suffix(".zip.sha256")
    zip_sha = sha256_bytes(zip_path.read_bytes())
    sha_path.write_text(f"{zip_sha}  {zip_path.name}\n", encoding="utf-8")
    receipt = {
        "package_id": f"{ROOT_EXECUTION_ID}_m0_blocker",
        "remote_zip_path": str(zip_path),
        "remote_sha256_path": str(sha_path),
        "zip_sha256": zip_sha,
        "zip_size": zip_path.stat().st_size,
        "zip_integrity": "PASS" if zipfile.is_zipfile(zip_path) else "FAIL",
        "manifest_sha256": sha256_bytes(manifest_path.read_bytes()),
        "manifest_entry_count": len(manifest),
        "expected_count": len(manifest) + 1,
        "present_count": len(zipfile.ZipFile(zip_path).namelist()),
        "secret_scan": pre_scan,
        "backup_dump_packaged": False,
        "evidence_package_status": "COMPLETE_SAFE_ARCHIVE_VERIFIED"
        if pre_scan["secret_hit_count"] == 0 and zipfile.is_zipfile(zip_path)
        else "INCOMPLETE_OR_UNSAFE",
    }
    receipt_path = ARCHIVE_DIR / f"{ROOT_EXECUTION_ID}_m0_blocker_remote_build_receipt.json"
    write_json(receipt_path, receipt)
    return receipt


def zero_counts(prefix: str, denominator: int, *, forecast_denominator: int | None = None) -> dict[str, Any]:
    forecast_denominator = denominator if forecast_denominator is None else forecast_denominator
    return {
        "official_source_status": f"{prefix}_OFFICIAL_SOURCE_VERIFIED_0_OF_{denominator}",
        "trusted_ingestion_status": f"{prefix}_TRUSTED_INGESTION_0_OF_{denominator}",
        "api_status": f"{prefix}_FORMAL_API_VERIFIED_0_OF_{denominator}",
        "triptych_browser_status": f"{prefix}_TRIPTYCH_BROWSER_VERIFIED_0_OF_{denominator}",
        "data_dashboard_status": f"{prefix}_DATA_DASHBOARD_COMPLETE_0_OF_{denominator}",
        "formal_forecast_status": f"{prefix}_FORMAL_FORECAST_PUBLISHED_0_OF_{forecast_denominator}",
        "forecast_page_status": f"{prefix}_FORECAST_PAGE_BROWSER_VERIFIED_0_OF_{forecast_denominator}",
        "evidence_package_status": "COMPLETE_SAFE_ARCHIVE_VERIFIED",
    }


def blocker_receipt(name: str, blocker: str, wave: str) -> dict[str, Any]:
    receipt = {
        "receipt_id": name,
        "root_execution_id": ROOT_EXECUTION_ID,
        "wave": wave,
        "state": "BLOCKED",
        "reason": blocker,
        "start_utc": None,
        "end_utc": None,
        "exit_code": None,
        "command_redacted": [],
        "stdout_path": None,
        "stderr_path": None,
        "evidence": "m0/PRODUCTION_WRITE_GATE.json",
    }
    return receipt


def write_wave_blocker_evidence(blockers: list[str]) -> dict[str, Any]:
    blocker = blockers[0] if blockers else "M0_PRODUCTION_WRITE_GATE_BLOCKED"
    for directory in (WAVE1_DIR, WAVE2_DIR, FINAL_DIR, FINAL_REPORTS_DIR):
        directory.mkdir(parents=True, exist_ok=True)
    wave1_counts = zero_counts("WAVE1_TWO_DISEASE", 2)
    wave2_counts = zero_counts("WAVE2_NIPAH", 1)
    wave2_counts["formal_forecast_status"] = "WAVE2_NIPAH_FORMAL_FORECAST_PUBLISHED_0_OF_1"
    wave2_counts["forecast_page_status"] = "WAVE2_NIPAH_FORECAST_PAGE_BROWSER_VERIFIED_0_OF_1"
    final_counts = zero_counts("THREE_DISEASE", 3)
    wave1_entries = []
    for disease in ("hantavirus", "legionella"):
        wave1_entries.append(
            {
                "disease": disease,
                "wave": "wave1",
                "official_source": {"state": "NOT_RUN", "reason": blocker, "counted": False},
                "trusted_ingestion": {"state": "NOT_RUN", "reason": blocker, "counted": False},
                "api": {"state": "NOT_RUN", "reason": blocker, "counted": False},
                "triptych_browser": {"state": "NOT_RUN", "reason": blocker, "counted": False},
                "data_dashboard": {"state": "NOT_RUN", "reason": blocker, "counted": False},
                "forecast_eligibility": {"state": "NOT_RUN", "reason": blocker},
                "formal_forecast": {"state": "NOT_RUN", "reason": blocker, "counted": False},
                "forecast_page": {"state": "NOT_RUN", "reason": blocker, "counted": False},
                "run_id": None,
                "evidence_refs": ["m0/PRODUCTION_WRITE_GATE.json", "m0/DATABASE_BACKUP_RECEIPT_WAVE1.json"],
                "exact_blocker": blocker,
            }
        )
    wave2_entries = [
        {
            "disease": "nipah",
            "wave": "wave2",
            "official_source": {"state": "NOT_RUN", "reason": blocker, "counted": False},
            "trusted_ingestion": {"state": "NOT_RUN", "reason": blocker, "counted": False},
            "api": {"state": "NOT_RUN", "reason": blocker, "counted": False},
            "triptych_browser": {"state": "NOT_RUN", "reason": blocker, "counted": False},
            "data_dashboard": {"state": "NOT_RUN", "reason": blocker, "counted": False},
            "forecast_eligibility": {"state": "NOT_ELIGIBLE", "reason": "NOT_ELIGIBLE_EVENT_ONLY"},
            "formal_forecast": {"state": "NOT_ELIGIBLE", "reason": "NOT_ELIGIBLE_EVENT_ONLY", "counted": False},
            "forecast_page": {"state": "NOT_ELIGIBLE", "reason": "NOT_ELIGIBLE_EVENT_ONLY", "counted": False},
            "run_id": None,
            "evidence_refs": ["m0/PRODUCTION_WRITE_GATE.json", "m0/DATABASE_BACKUP_RECEIPT_WAVE1.json"],
            "exact_blocker": blocker,
        }
    ]
    write_json(WAVE1_DIR / "WAVE1_STATUS_LEDGER.json", {"root_execution_id": ROOT_EXECUTION_ID, "entries": wave1_entries})
    write_json(WAVE1_DIR / "WAVE1_STATUS_COUNTS.json", wave1_counts)
    write_text(WAVE1_DIR / "WAVE1_RELEASE_REPORT.md", f"# Wave 1 release report\n\nstate=BLOCKED\nreason={blocker}\n")
    write_text(WAVE1_DIR / "WAVE1_EXECUTION_PLAN.md", "Wave 1 production operations were not run because M0 production write gate was blocked.\n")
    write_json(WAVE1_DIR / "PRODUCTION_LOCK_RECEIPT_WAVE1.json", blocker_receipt("PRODUCTION_LOCK_RECEIPT_WAVE1", blocker, "wave1"))
    write_json(WAVE1_DIR / "DEPLOYMENT_WAVE_1_RECEIPT.json", blocker_receipt("DEPLOYMENT_WAVE_1_RECEIPT", blocker, "wave1"))
    write_json(WAVE1_DIR / "ROLLBACK_RECEIPT_AGGREGATE.json", {"rollback_status": "NOT_NEEDED", "reason": "NO_PRODUCTION_MUTATION_PERFORMED"})
    write_json(WAVE1_DIR / "TEST_SUMMARY.json", {"command_attempts_total": 0, "command_attempts_passed": 0, "command_attempts_failed": 0, "command_attempts_skipped": 0, "command_attempts_blocked": 1, "unique_test_cases_total": 0, "final_test_cases_passed": 0, "final_test_cases_failed": 0, "final_test_cases_skipped": 0, "final_test_cases_blocked": 1})
    write_json(WAVE1_DIR / "TEST_EXECUTION_INDEX.json", [])
    write_json(WAVE2_DIR / "WAVE2_STATUS_LEDGER.json", {"root_execution_id": ROOT_EXECUTION_ID, "entries": wave2_entries})
    write_json(WAVE2_DIR / "WAVE2_STATUS_COUNTS.json", wave2_counts)
    write_text(WAVE2_DIR / "WAVE2_RELEASE_REPORT.md", f"# Wave 2 release report\n\nstate=BLOCKED\nreason={blocker}\n")
    write_text(WAVE2_DIR / "WAVE2_EXECUTION_PLAN.md", "Wave 2 production operations were not run because M0 production write gate was blocked.\n")
    write_json(WAVE2_DIR / "PRODUCTION_LOCK_RECEIPT_WAVE2.json", blocker_receipt("PRODUCTION_LOCK_RECEIPT_WAVE2", blocker, "wave2"))
    write_json(WAVE2_DIR / "DEPLOYMENT_WAVE_2_RECEIPT.json", blocker_receipt("DEPLOYMENT_WAVE_2_RECEIPT", blocker, "wave2"))
    write_json(WAVE2_DIR / "ROLLBACK_RECEIPT_AGGREGATE.json", {"rollback_status": "NOT_NEEDED", "reason": "NO_PRODUCTION_MUTATION_PERFORMED"})
    write_json(WAVE2_DIR / "TEST_SUMMARY.json", {"command_attempts_total": 0, "command_attempts_passed": 0, "command_attempts_failed": 0, "command_attempts_skipped": 0, "command_attempts_blocked": 1, "unique_test_cases_total": 0, "final_test_cases_passed": 0, "final_test_cases_failed": 0, "final_test_cases_skipped": 0, "final_test_cases_blocked": 1})
    write_json(WAVE2_DIR / "TEST_EXECUTION_INDEX.json", [])
    final_entries = wave1_entries + wave2_entries
    write_json(FINAL_DIR / "FINAL_STATUS_LEDGER.json", {"root_execution_id": ROOT_EXECUTION_ID, "entries": final_entries})
    write_json(FINAL_DIR / "FINAL_STATUS_COUNTS.json", final_counts)
    write_json(FINAL_DIR / "FINAL_TEST_SUMMARY.json", {"command_attempts_total": 0, "command_attempts_passed": 0, "command_attempts_failed": 0, "command_attempts_skipped": 0, "command_attempts_blocked": 1, "unique_test_cases_total": 0, "final_test_cases_passed": 0, "final_test_cases_failed": 0, "final_test_cases_skipped": 0, "final_test_cases_blocked": 1})
    write_text(
        FINAL_REPORTS_DIR / "FINAL_TWO_WAVE_RELEASE_REPORT.md",
        f"# Final two-wave release report\n\nstate=BLOCKED\nreason={blocker}\nNo production migration, promotion, forecast publication, service restart, or password/auth change was performed.\n",
    )
    return {"wave1_counts": wave1_counts, "wave2_counts": wave2_counts, "final_counts": final_counts, "blocker": blocker}


def package_named(package_id: str, include_roots: list[Path], manifest_path: Path) -> dict[str, Any]:
    files = []
    for root in include_roots:
        if not root.exists():
            continue
        files.extend([p for p in root.rglob("*") if p.is_file() and ARCHIVE_DIR not in p.parents and ".dump" not in p.name])
    files = sorted(set(files))
    scan = secret_scan(files)
    manifest_entries = []
    for path in files:
        if path == manifest_path:
            continue
        rel = path.relative_to(RUN_ROOT).as_posix()
        st = os.lstat(path)
        manifest_entries.append(
            {
                "path": rel,
                "type": "file",
                "size": st.st_size,
                "mode": oct(stat.S_IMODE(st.st_mode)),
                "sha256": sha256_bytes(path.read_bytes()),
                "source_wave": package_id,
                "generation_stage": "BLOCKER_FINALIZATION",
                "required": True,
                "sensitivity": "INTERNAL_NONSECRET",
                "producer": "two_wave_prod_control_remote.py",
                "category": "blocked_evidence",
            }
        )
    write_json(manifest_path, {"manifest_self_excluded": True, "entries": manifest_entries})
    zip_path = ARCHIVE_DIR / f"{package_id}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for entry in manifest_entries:
            zf.write(RUN_ROOT / entry["path"], arcname=entry["path"])
        zf.write(manifest_path, arcname=manifest_path.relative_to(RUN_ROOT).as_posix())
    zip_sha = sha256_bytes(zip_path.read_bytes())
    sha_path = zip_path.with_suffix(".zip.sha256")
    sha_path.write_text(f"{zip_sha}  {zip_path.name}\n", encoding="utf-8")
    receipt = {
        "package_id": package_id,
        "remote_zip_path": str(zip_path),
        "remote_sha256_path": str(sha_path),
        "zip_sha256": zip_sha,
        "zip_size": zip_path.stat().st_size,
        "zip_integrity": "PASS" if zipfile.is_zipfile(zip_path) else "FAIL",
        "manifest_path": str(manifest_path),
        "manifest_sha256": sha256_bytes(manifest_path.read_bytes()),
        "manifest_entry_count": len(manifest_entries),
        "expected_count": len(manifest_entries) + 1,
        "present_count": len(zipfile.ZipFile(zip_path).namelist()),
        "secret_scan": scan,
        "backup_dump_packaged": False,
        "evidence_package_status": "COMPLETE_SAFE_ARCHIVE_VERIFIED"
        if scan["secret_hit_count"] == 0 and zipfile.is_zipfile(zip_path)
        else "INCOMPLETE_OR_UNSAFE",
    }
    write_json(ARCHIVE_DIR / f"{package_id}_remote_build_receipt.json", receipt)
    return receipt


def package_wave_and_final_blockers() -> dict[str, Any]:
    wave1 = package_named(WAVE1_EXECUTION_ID, [M0_DIR, LOG_DIR, WAVE1_DIR], WAVE1_DIR / "PACKAGE_MANIFEST.json")
    wave2 = package_named(WAVE2_EXECUTION_ID, [M0_DIR, LOG_DIR, WAVE1_DIR, WAVE2_DIR], WAVE2_DIR / "PACKAGE_MANIFEST.json")
    final = package_named(FINAL_PACKAGE_ID, [M0_DIR, LOG_DIR, WAVE1_DIR, WAVE2_DIR, FINAL_DIR, FINAL_REPORTS_DIR], FINAL_DIR / "PACKAGE_MANIFEST.json")
    return {"wave1": wave1, "wave2": wave2, "final": final}


def main() -> None:
    if RUN_ROOT.exists():
        raise SystemExit(f"BLOCKED_ISOLATION_RESOURCE_COLLISION:{RUN_ROOT}")
    for directory in (M0_DIR, LOG_DIR, ARCHIVE_DIR, BACKUP_DIR):
        directory.mkdir(parents=True, exist_ok=False)
    os.chmod(RUN_ROOT, 0o700)
    blockers: list[str] = []
    write_json(
        M0_DIR / "EXECUTION_IDS.json",
        {
            "root_execution_id": ROOT_EXECUTION_ID,
            "wave1_execution_id": WAVE1_EXECUTION_ID,
            "wave2_execution_id": WAVE2_EXECUTION_ID,
            "final_package_id": FINAL_PACKAGE_ID,
            "run_root": str(RUN_ROOT),
            "project": str(PROJECT),
        },
    )
    dirty = git_info()
    db = engine()
    with db.connect() as conn:
        before_db = db_fingerprint(conn)
        write_json(M0_DIR / "DATABASE_FINGERPRINT_BEFORE.json", before_db)
        before_protected = protected_runs(conn)
        write_json(M0_DIR / "PROTECTED_RUN_FINGERPRINT_BEFORE.json", before_protected)
        service = service_state(dirty)
        write_json(M0_DIR / "SERVICE_STATE_BEFORE.json", service)
        readiness = readiness_root_cause(service, conn)
        write_json(
            M0_DIR / "BASELINE_DEFECT_REPRODUCTION.json",
            {
                "service_runtime_reproducibility_state": service["service_runtime_reproducibility_state"],
                "readiness": readiness,
                "http_probe": service["http_probe"],
                "three_disease_catalog": before_db.get("three_disease_catalog"),
            },
        )
        if service["service_runtime_reproducibility_state"] != "PASS":
            blockers.append(service["service_runtime_reproducibility_state"])
        write_json(M0_DIR / "PRIOR_PACKAGE_FINGERPRINT_BEFORE.json", prior_package_fingerprint())
        backup = backup_and_restore(conn)
        if backup.get("state") != "PASS":
            blockers.append(str(backup.get("reason") or "BLOCKED_DATABASE_BACKUP_UNVERIFIED"))
        if backup.get("isolation_role_write_separation_state") == "BLOCKED":
            blockers.append("BLOCKED_DATABASE_ISOLATION_ROLE_NOT_ESTABLISHED")
    write_json(
        M0_DIR / "PRODUCTION_WRITE_GATE.json",
        {
            "state": "BLOCKED" if blockers else "PASS",
            "blockers": blockers,
            "production_promotion_allowed": not blockers,
            "wave1_allowed": not blockers,
            "wave2_allowed": not blockers,
        },
    )
    status_ledger(blockers)
    write_text(
        M0_DIR / "EXECUTION_PLAN.md",
        "# Two-wave execution plan\n\n"
        f"- root_execution_id: `{ROOT_EXECUTION_ID}`\n"
        f"- wave1_execution_id: `{WAVE1_EXECUTION_ID}`\n"
        f"- wave2_execution_id: `{WAVE2_EXECUTION_ID}`\n"
        f"- final_package_id: `{FINAL_PACKAGE_ID}`\n"
        f"- production_write_gate: {'BLOCKED' if blockers else 'PASS'}\n"
        f"- blockers: `{', '.join(blockers) if blockers else 'none'}`\n\n"
        "If the production write gate is BLOCKED, only isolation evidence work may continue; production migration, promotion, publication, and service restart are forbidden.\n",
    )
    blocker_evidence = write_wave_blocker_evidence(blockers) if blockers else {}
    receipt = package_m0()
    wave_final_packages = package_wave_and_final_blockers() if blockers else {}
    final = {
        "root_execution_id": ROOT_EXECUTION_ID,
        "wave1_execution_id": WAVE1_EXECUTION_ID,
        "wave2_execution_id": WAVE2_EXECUTION_ID,
        "final_package_id": FINAL_PACKAGE_ID,
        "state": "BLOCKED" if blockers else "M0_PASS",
        "blockers": blockers,
        "blocker_evidence": blocker_evidence,
        "m0_package": receipt,
        "wave_final_packages": wave_final_packages,
    }
    write_json(RUN_ROOT / "M0_FINAL_STATUS.json", final)
    print(json.dumps(final, ensure_ascii=False, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
