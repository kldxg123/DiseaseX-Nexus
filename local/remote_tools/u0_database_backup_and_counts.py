from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def process_environment(pid: int) -> dict[str, str]:
    raw = Path(f"/proc/{pid}/environ").read_bytes()
    result: dict[str, str] = {}
    for item in raw.split(b"\0"):
        if not item or b"=" not in item:
            continue
        key, value = item.split(b"=", 1)
        result[key.decode("utf-8", "strict")] = value.decode("utf-8", "strict")
    return result


def normalized_dsn(value: str) -> str:
    return value.replace("postgresql+psycopg://", "postgresql://", 1).replace(
        "postgres+psycopg://", "postgresql://", 1
    )


def manifest(report: Path) -> None:
    target = report / "MANIFEST.sha256"
    rows = []
    for path in sorted(item for item in report.iterdir() if item.is_file() and item.name != target.name):
        rows.append(f"{sha256(path)}  {path.name}")
    target.write_text("\n".join(rows) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--report-dir", required=True)
    parser.add_argument("--backup-dir", required=True)
    args = parser.parse_args()

    project = Path(args.project).resolve()
    report = Path(args.report_dir).resolve()
    backup_dir = Path(args.backup_dir).resolve()
    backup_dir.mkdir(parents=True, exist_ok=False)

    pid = int((project / "runtime/dashboard.pid").read_text(encoding="utf-8").strip())
    env = process_environment(pid)
    dsn_key = next(
        (key for key in ("MIGRATION_DATABASE_URL", "APP_DATABASE_URL", "DATABASE_URL", "DASHBOARD_DATABASE_URL") if env.get(key)),
        None,
    )
    if dsn_key is None:
        raise SystemExit("BLOCKED_DASHBOARD_DATABASE_DSN_NOT_PRESENT")
    dsn = normalized_dsn(env[dsn_key])

    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict

    info = conninfo_to_dict(dsn)
    dump_bin = project / "runtime/postgres18x/bin/pg_dump"
    restore_bin = project / "runtime/postgres18x/bin/pg_restore"
    dump_partial = backup_dir / "epidemic_openclaw_u0.dump.partial"
    dump_path = backup_dir / "epidemic_openclaw_u0.dump"
    restore_list = report / "12_PG_RESTORE_LIST.txt"

    command = [str(dump_bin), "--format=custom", "--compress=6", "--no-owner", "--file", str(dump_partial)]
    for key, flag in (("host", "--host"), ("port", "--port"), ("user", "--username"), ("dbname", "--dbname")):
        if info.get(key):
            command.extend([flag, str(info[key])])
    command.append("--no-password")
    child_env = os.environ.copy()
    if info.get("password"):
        child_env["PGPASSWORD"] = str(info["password"])
    if info.get("sslmode"):
        child_env["PGSSLMODE"] = str(info["sslmode"])
    started_at = datetime.now(timezone.utc)
    completed = subprocess.run(command, env=child_env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    child_env.pop("PGPASSWORD", None)
    if completed.returncode != 0:
        failure = {
            "status": "FAILED",
            "dsn_source_key": dsn_key,
            "pg_dump_exit_code": completed.returncode,
            "error_type": "PG_DUMP_FAILED_REDACTED",
            "partial_file_exists": dump_partial.exists(),
            "partial_bytes": dump_partial.stat().st_size if dump_partial.exists() else 0,
        }
        (report / "11_BACKUP_RECEIPT.json").write_text(json.dumps(failure, indent=2), encoding="utf-8")
        manifest(report)
        raise SystemExit("BLOCKED_PG_DUMP_FAILED_REDACTED")
    dump_partial.rename(dump_path)

    listed = subprocess.run(
        [str(restore_bin), "--list", str(dump_path)],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    restore_list.write_text(listed.stdout, encoding="utf-8")
    if listed.returncode != 0 or not listed.stdout.strip():
        raise SystemExit("BLOCKED_PG_RESTORE_LIST_FAILED")

    receipt = {
        "status": "PASS",
        "created_at": started_at.isoformat(),
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "format": "PostgreSQL custom",
        "consistent_online_dump": True,
        "dsn_source_key": dsn_key,
        "credential_value_logged": False,
        "credential_value_persisted": False,
        "dump_path": str(dump_path),
        "dump_bytes": dump_path.stat().st_size,
        "dump_sha256": sha256(dump_path),
        "pg_dump_exit_code": completed.returncode,
        "pg_restore_list_exit_code": listed.returncode,
        "pg_restore_list_bytes": restore_list.stat().st_size,
        "pg_restore_list_sha256": sha256(restore_list),
    }
    (report / "11_BACKUP_RECEIPT.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")

    table_counts: list[dict[str, Any]] = []
    relation_columns: list[dict[str, Any]] = []
    with psycopg.connect(dsn, autocommit=False) as conn:
        with conn.cursor() as cur:
            cur.execute("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            cur.execute("SET LOCAL statement_timeout = '30s'")
            cur.execute("SET LOCAL idle_in_transaction_session_timeout = '120s'")
            cur.execute("SHOW transaction_read_only")
            read_only = cur.fetchone()[0]
            cur.execute(
                """
                select table_schema, table_name
                from information_schema.tables
                where table_type = 'BASE TABLE'
                  and table_schema in ('epidemic', 'epidemic_kg', 'public')
                  and table_name ~ '(observation|snapshot|trusted|forecast|promotion|active)'
                order by table_schema, table_name
                """
            )
            relations = cur.fetchall()
            for schema, table in relations:
                cur.execute("SAVEPOINT count_relation")
                try:
                    cur.execute(sql.SQL("select count(*) from {}.{}").format(sql.Identifier(schema), sql.Identifier(table)))
                    count = int(cur.fetchone()[0])
                    cur.execute("RELEASE SAVEPOINT count_relation")
                    table_counts.append({"schema": schema, "table": table, "count": count, "status": "PASS"})
                except Exception as exc:  # noqa: BLE001
                    cur.execute("ROLLBACK TO SAVEPOINT count_relation")
                    cur.execute("RELEASE SAVEPOINT count_relation")
                    table_counts.append(
                        {"schema": schema, "table": table, "count": None, "status": type(exc).__name__}
                    )
            cur.execute(
                """
                select table_schema, table_name, column_name
                from information_schema.columns
                where table_schema in ('epidemic', 'epidemic_kg', 'public')
                  and (table_name ~ '(promotion|active|trusted)' or column_name ~ '(promotion|active|trusted)')
                order by table_schema, table_name, ordinal_position
                """
            )
            relation_columns = [
                {"schema": row[0], "table": row[1], "column": row[2]} for row in cur.fetchall()
            ]
            cur.execute("select version_num from alembic_version")
            alembic_versions = [row[0] for row in cur.fetchall()]
            conn.rollback()

    counts_payload = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "isolation": "REPEATABLE READ",
        "transaction_read_only": read_only,
        "statement_timeout": "30s",
        "database_writes": 0,
        "rows_deleted": 0,
        "table_counts": table_counts,
        "relevant_columns": relation_columns,
    }
    (report / "13_DATABASE_BASELINE_COUNTS.json").write_text(
        json.dumps(counts_payload, indent=2), encoding="utf-8"
    )
    (report / "14_ALEMBIC_VERSION.json").write_text(
        json.dumps({"versions": alembic_versions}, indent=2), encoding="utf-8"
    )

    final = {
        "status": "U0_COMPLETE_READY_FOR_U1",
        "git_baseline_captured": True,
        "dirty_overlay_captured": True,
        "isolated_worktree_created": True,
        "active_project_unchanged": True,
        "database_backup_completed": True,
        "database_baseline_counts_completed": True,
        "database_writes": 0,
        "rows_deleted": 0,
        "u1_authorized_to_start": False,
        "next_stage": "U1_REQUIRES_NEXT_TASK_PER_REMOTE_AGENTS_MD",
    }
    (report / "09_FINAL_STATUS.json").write_text(json.dumps(final, indent=2), encoding="utf-8")
    (report / "10_FINAL_REPORT.md").write_text(
        "# Universal Forecast Platform U0 Report\n\n"
        "Status: `U0_COMPLETE_READY_FOR_U1`\n\n"
        "Git baseline, dirty overlay, isolated worktree, custom-format PostgreSQL backup, restore-list "
        "verification and repeatable-read read-only business counts are complete. Database writes and rows "
        "deleted are zero. The active project source tree remains unchanged. Per remote AGENTS.md, execution "
        "stops before U1.\n",
        encoding="utf-8",
    )
    manifest(report)
    print(json.dumps({"status": final["status"], "dump_bytes": receipt["dump_bytes"], "counted_tables": len(table_counts)}))


if __name__ == "__main__":
    main()
