#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path("USER_HOME/projects/openclaw")
PG = PROJECT / "runtime" / "postgres18x" / "bin"
SOCKET = PROJECT / "runtime" / "postgres-run"
TASK_ID = "EPIDEMIC_KG_PREDICTION_R4_SCHEMA_EVIDENCE_ORCHESTRATOR_SHADOW"


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def run(cmd: list[str], timeout: int = 600) -> dict:
    started = now()
    p = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout)
    return {"cmd": [str(c) for c in cmd], "started_at_utc": started, "ended_at_utc": now(), "returncode": p.returncode, "stdout": p.stdout[-8000:], "stderr": p.stderr[-8000:]}


def main() -> int:
    report = Path(os.environ["R4_REPORT_DIR"])
    report.mkdir(parents=True, exist_ok=True)
    dbname = os.environ.get("R4_ISO_DB", "r4_iso_shadow")
    backup = report / f"{report.name}_epidemic_openclaw.pgcustom"
    payload = {
        "schema_version": "1.0",
        "task_id": TASK_ID,
        "execution_id": report.name,
        "generated_at_utc": now(),
        "active_project_root": str(PROJECT),
        "database_identity_without_secret": {"source_database": "epidemic_openclaw", "restore_database": dbname, "port": 55432},
        "result_status": "STARTED",
        "commands_or_evidence_refs": [],
        "blockers": [],
        "no_fake_pass_assertion": True,
    }
    dump = run([PG / "pg_dump", "-h", SOCKET, "-p", "55432", "-U", "postgres", "-Fc", "-f", backup, "epidemic_openclaw"], timeout=900)
    payload["dump"] = dump
    if dump["returncode"] != 0:
        payload["result_status"] = "BACKUP_FAILED"
        (report / "R4_DATABASE_BACKUP_RESTORE.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return 1
    payload["backup_path"] = str(backup)
    payload["backup_size"] = backup.stat().st_size
    payload["backup_sha256"] = sha_file(backup)
    payload["restore_list"] = run([PG / "pg_restore", "--list", backup], timeout=120)
    # Use a fresh R4 isolation DB name supplied by the caller. If it exists, stop.
    exists = run([PG / "psql", "-h", SOCKET, "-p", "55432", "-U", "postgres", "-d", "postgres", "-Atc", f"select 1 from pg_database where datname='{dbname}'"], timeout=60)
    payload["isolation_exists_probe"] = exists
    if exists["stdout"].strip() == "1":
        payload["result_status"] = "BLOCKED_ISOLATION_DB_ALREADY_EXISTS"
        (report / "R4_DATABASE_BACKUP_RESTORE.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return 2
    createdb = run([PG / "psql", "-h", SOCKET, "-p", "55432", "-U", "postgres", "-d", "postgres", "-c", f'create database "{dbname}"'], timeout=60)
    payload["createdb"] = createdb
    if createdb["returncode"] != 0:
        payload["result_status"] = "CREATE_ISOLATION_DB_FAILED"
        (report / "R4_DATABASE_BACKUP_RESTORE.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return 3
    restore = run([PG / "pg_restore", "-h", SOCKET, "-p", "55432", "-U", "postgres", "-d", dbname, "--no-owner", backup], timeout=900)
    payload["restore"] = restore
    if restore["returncode"] != 0:
        payload["result_status"] = "RESTORE_FAILED"
        (report / "R4_DATABASE_BACKUP_RESTORE.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return 4
    verify = run([PG / "psql", "-h", SOCKET, "-p", "55432", "-U", "postgres", "-d", dbname, "-Atc", "select current_database(), (select version_num from epidemic.alembic_version limit 1), (select count(*) from epidemic_kg.kg_document_pages), (select count(*) from epidemic_kg.kg_document_chunks), (select count(*) from epidemic_kg.kg_pending_claim_candidates)"], timeout=60)
    payload["restore_verify"] = verify
    payload["result_status"] = "BACKUP_RESTORE_VERIFIED" if verify["returncode"] == 0 else "RESTORE_VERIFY_FAILED"
    (report / "R4_DATABASE_BACKUP_RESTORE.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": payload["result_status"], "backup_sha256": payload.get("backup_sha256"), "iso_db": dbname}, ensure_ascii=False))
    return 0 if payload["result_status"] == "BACKUP_RESTORE_VERIFIED" else 5


if __name__ == "__main__":
    raise SystemExit(main())
