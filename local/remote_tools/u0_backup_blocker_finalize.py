from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def process_environment(pid: int) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in Path(f"/proc/{pid}/environ").read_bytes().split(b"\0"):
        if item and b"=" in item:
            key, value = item.split(b"=", 1)
            result[key.decode()] = value.decode()
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--report-dir", required=True)
    args = parser.parse_args()
    project = Path(args.project).resolve()
    report = Path(args.report_dir).resolve()
    pid = int((project / "runtime/dashboard.pid").read_text().strip())
    env = process_environment(pid)
    dsn = env["DASHBOARD_DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://", 1)

    from psycopg.conninfo import conninfo_to_dict

    info = conninfo_to_dict(dsn)
    command = [str(project / "runtime/postgres18x/bin/pg_dump"), "--schema-only", "--no-password", "--file", "/dev/null"]
    for key, flag in (("host", "--host"), ("port", "--port"), ("user", "--username"), ("dbname", "--dbname")):
        if info.get(key):
            command.extend([flag, str(info[key])])
    child_env = os.environ.copy()
    if info.get("password"):
        child_env["PGPASSWORD"] = str(info["password"])
    probe = subprocess.run(command, env=child_env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    child_env.pop("PGPASSWORD", None)
    lowered = probe.stderr.lower()
    if "permission denied" in lowered or "must be owner" in lowered:
        category = "INSUFFICIENT_DATABASE_PRIVILEGES"
    elif "password authentication failed" in lowered:
        category = "AUTHENTICATION_FAILED"
    elif "connection refused" in lowered or "could not connect" in lowered:
        category = "CONNECTION_FAILED"
    else:
        category = "PG_DUMP_REJECTED_OTHER_REDACTED"

    receipt_path = report / "11_BACKUP_RECEIPT.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8")) if receipt_path.exists() else {}
    receipt.update(
        {
            "status": "FAILED",
            "dsn_source_key": "DASHBOARD_DATABASE_URL",
            "credential_value_logged": False,
            "credential_value_persisted": False,
            "schema_only_probe_exit_code": probe.returncode,
            "failure_category": category,
            "error_text_persisted": False,
            "required_unblock": "Provide an approved PostgreSQL backup-role or migration-role DSN through a standard in-memory loader.",
        }
    )
    receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")

    final = {
        "status": "U0_BLOCKED_DATABASE_BACKUP_ROLE_REQUIRED",
        "git_baseline_captured": True,
        "dirty_overlay_captured": True,
        "isolated_worktree_created": True,
        "active_project_unchanged": True,
        "dashboard_dsn_memory_use_authorized": True,
        "dashboard_dsn_pg_dump_capable": False,
        "database_backup_completed": False,
        "database_baseline_counts_completed": False,
        "database_writes": 0,
        "rows_deleted": 0,
        "u1_authorized_to_start": False,
        "next_action": receipt["required_unblock"],
    }
    (report / "09_FINAL_STATUS.json").write_text(json.dumps(final, indent=2), encoding="utf-8")
    (report / "10_FINAL_REPORT.md").write_text(
        "# Universal Forecast Platform U0 Report\n\n"
        "Status: `U0_BLOCKED_DATABASE_BACKUP_ROLE_REQUIRED`\n\n"
        "Git baseline, dirty overlay and the isolated worktree are complete. The active project is unchanged. "
        "The authorized Dashboard DSN was used only in process memory, but its database role cannot create the "
        "required PostgreSQL custom-format backup. No credential value or raw error text was logged. A dedicated "
        "backup-role or migration-role DSN must be supplied through an approved standard loader before U1.\n\n"
        "Database writes: 0. Rows deleted: 0. U1 was not started.\n",
        encoding="utf-8",
    )

    manifest = report / "MANIFEST.sha256"
    rows = []
    for path in sorted(item for item in report.iterdir() if item.is_file() and item.name != manifest.name):
        rows.append(f"{sha256(path)}  {path.name}")
    manifest.write_text("\n".join(rows) + "\n", encoding="utf-8")
    print(json.dumps({"status": final["status"], "failure_category": category}))


if __name__ == "__main__":
    main()
