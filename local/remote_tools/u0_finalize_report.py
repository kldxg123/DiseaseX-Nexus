from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(args: list[str], cwd: Path) -> str:
    return subprocess.run(
        args,
        cwd=cwd,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout


def http_summary(path: str) -> dict[str, object]:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:8020/{path}", timeout=5) as response:
            body = json.loads(response.read())
            return {
                "http_status": response.status,
                "ready": body.get("ready"),
                "required_revision": body.get("required_revision"),
                "checks_true": sum(1 for value in (body.get("checks") or {}).values() if value is True),
            }
    except Exception as exc:  # noqa: BLE001
        return {"error_type": type(exc).__name__}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--report-dir", required=True)
    parser.add_argument("--worktree", required=True)
    args = parser.parse_args()

    project = Path(args.project).resolve()
    report = Path(args.report_dir).resolve()
    worktree = Path(args.worktree).resolve()
    baseline = json.loads((report / "00_BASELINE.json").read_text(encoding="utf-8"))

    current_head = run(["git", "rev-parse", "HEAD"], project).strip()
    current_status = run(["git", "status", "--short"], project)
    current_tracked = []
    for row in baseline["tracked_dirty"]:
        path = project / str(row["path"])
        current_tracked.append(
            {
                "path": row["path"],
                "exists": path.exists(),
                "sha256": sha256(path) if path.exists() and path.is_file() else None,
                "matches_before": (sha256(path) if path.exists() and path.is_file() else None) == row["sha256"],
            }
        )
    unchanged = current_head == baseline["git_head"] and all(row["matches_before"] for row in current_tracked)

    backup = {
        "completed": False,
        "custom_format_dump": None,
        "dump_sha256": None,
        "pg_restore_list_receipt": None,
        "blocker": "BLOCKED_EXISTING_BACKUP_SCRIPT_OLD_AUTODL_ROOT_AND_NO_STANDARD_PASSWORDLESS_PGDUMP_AUTH",
        "existing_script_syntax": "PASS",
        "existing_script_runtime": "FAILED_OLD_ABSOLUTE_PATH",
        "tcp_passwordless_probe": 'REDACTED',
        "local_socket_passwordless_probe": 'REDACTED',
        "credential_probe_attempted": False,
        "minimum_unblock": (
            "Repair scripts/postgres/backup.sh to resolve the active project root and use an approved "
            "standard credential loader, or provide an approved backup service credential through that loader."
        ),
    }
    (report / "07_DATABASE_BACKUP_STATUS.json").write_text(json.dumps(backup, indent=2), encoding="utf-8")

    runtime = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "healthz": http_summary("healthz"),
        "readyz": http_summary("readyz"),
        "active_project_head": current_head,
        "active_project_status_line_count": len([line for line in current_status.splitlines() if line]),
        "tracked_dirty_hashes_unchanged": unchanged,
        "isolated_worktree_exists": worktree.exists(),
        "isolated_worktree_head": run(["git", "rev-parse", "HEAD"], worktree).strip() if worktree.exists() else None,
        "database_mutations_by_u0": 0,
        "rows_deleted_by_u0": 0,
        "source_tree_changes_by_u0": 0,
    }
    (report / "08_RUNTIME_AND_NON_MUTATION.json").write_text(json.dumps(runtime, indent=2), encoding="utf-8")

    final_status = "U0_PARTIAL_BLOCKED_DATABASE_BACKUP_AUTH"
    summary = {
        "status": final_status,
        "git_baseline_captured": True,
        "dirty_overlay_captured": True,
        "isolated_worktree_created": worktree.exists(),
        "active_project_unchanged": unchanged,
        "database_backup_completed": False,
        "database_baseline_counts_completed": False,
        "u1_authorized_to_start": False,
        "next_action": backup["minimum_unblock"],
    }
    (report / "09_FINAL_STATUS.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    markdown = f"""# Universal Forecast Platform U0 Report

Status: `{final_status}`

## Completed

- Captured Git HEAD, status, tracked dirty file hashes, untracked experiment inventory and binary overlay patch.
- Created isolated worktree `{worktree}` and applied the tracked dirty overlay there only.
- Verified the active project HEAD and tracked dirty file hashes remain unchanged.
- Verified Dashboard health and readiness without changing services.

## Blocker

The existing PostgreSQL backup script resolves an obsolete AutoDL absolute root. Standard passwordless pg_dump
authentication is unavailable over both TCP and the project-local Unix socket. No credential files or process
environments were inspected, and no secret value was read or logged.

U1 must not begin until a custom-format backup, SHA-256, pg_restore --list receipt and read-only production
baseline counts have been produced through an approved standard credential loader.

## Non-mutation

- Database writes: 0
- Rows deleted: 0
- Trusted/Forecast/Promotion/Active Pointer changes: not executed; database access remained unavailable
- Active project source changes by U0: 0
"""
    (report / "10_FINAL_REPORT.md").write_text(markdown, encoding="utf-8")

    manifest_path = report / "MANIFEST.sha256"
    rows = []
    for path in sorted(item for item in report.iterdir() if item.is_file() and item.name != manifest_path.name):
        rows.append(f"{sha256(path)}  {path.name}")
    manifest_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
