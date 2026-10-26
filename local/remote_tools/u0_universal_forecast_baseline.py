from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def run(args: list[str], cwd: Path, *, check: bool = True) -> str:
    completed = subprocess.run(
        args,
        cwd=cwd,
        check=check,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return completed.stdout


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_inventory(root: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    if not root.exists():
        return rows
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        rel = path.relative_to(root.parent).as_posix()
        rows.append({"path": rel, "bytes": path.stat().st_size, "sha256": sha256(path)})
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--report-dir", required=True)
    parser.add_argument("--worktree", required=True)
    parser.add_argument("--branch", required=True)
    args = parser.parse_args()

    project = Path(args.project).resolve()
    report = Path(args.report_dir).resolve()
    worktree = Path(args.worktree).resolve()
    report.mkdir(parents=True, exist_ok=False)

    head = run(["git", "rev-parse", "HEAD"], project).strip()
    status = run(["git", "status", "--short"], project)
    diff = run(["git", "diff", "--binary", "--no-ext-diff"], project)
    diff_stat = run(["git", "diff", "--stat"], project)

    tracked_dirty = run(["git", "diff", "--name-only", "--diff-filter=ACDMRTUXB"], project).splitlines()
    tracked_rows = []
    for rel in sorted(set(tracked_dirty)):
        path = project / rel
        tracked_rows.append(
            {
                "path": rel,
                "exists": path.exists(),
                "bytes": path.stat().st_size if path.exists() else None,
                "sha256": sha256(path) if path.exists() and path.is_file() else None,
            }
        )

    untracked_experiments = safe_inventory(project / "experiments")
    baseline = {
        "task": "KG_UNIVERSAL_FORECAST_U0",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "project": str(project),
        "git_head": head,
        "git_status_line_count": len([line for line in status.splitlines() if line]),
        "tracked_dirty": tracked_rows,
        "untracked_experiment_file_count": len(untracked_experiments),
        "untracked_experiment_total_bytes": sum(int(row["bytes"]) for row in untracked_experiments),
        "source_tree_modified_by_u0": False,
        "database_writes_by_u0": 0,
        "rows_deleted_by_u0": 0,
    }
    (report / "00_BASELINE.json").write_text(json.dumps(baseline, indent=2), encoding="utf-8")
    (report / "01_GIT_STATUS.txt").write_text(status, encoding="utf-8")
    (report / "02_TRACKED_DIRTY_FILES.json").write_text(
        json.dumps(tracked_rows, indent=2), encoding="utf-8"
    )
    (report / "03_UNTRACKED_EXPERIMENTS.json").write_text(
        json.dumps(untracked_experiments, indent=2), encoding="utf-8"
    )
    (report / "04_DIRTY_OVERLAY.patch").write_text(diff, encoding="utf-8")
    (report / "05_DIRTY_DIFF_STAT.txt").write_text(diff_stat, encoding="utf-8")

    if worktree.exists():
        raise SystemExit("BLOCKED_WORKTREE_ALREADY_EXISTS")
    branches = run(["git", "branch", "--format=%(refname:short)"], project).splitlines()
    if args.branch in branches:
        raise SystemExit("BLOCKED_BRANCH_ALREADY_EXISTS")
    run(["git", "worktree", "add", "-b", args.branch, str(worktree), head], project)
    overlay_applied = False
    if diff.strip():
        patch_path = report / "04_DIRTY_OVERLAY.patch"
        subprocess.run(["git", "apply", str(patch_path)], cwd=worktree, check=True)
        overlay_applied = True

    worktree_state = {
        "worktree": str(worktree),
        "branch": args.branch,
        "head": run(["git", "rev-parse", "HEAD"], worktree).strip(),
        "overlay_applied": overlay_applied,
        "overlay_sha256": sha256(report / "04_DIRTY_OVERLAY.patch"),
        "status": run(["git", "status", "--short"], worktree).splitlines(),
        "active_project_head_unchanged": run(["git", "rev-parse", "HEAD"], project).strip() == head,
    }
    (report / "06_ISOLATED_WORKTREE.json").write_text(
        json.dumps(worktree_state, indent=2), encoding="utf-8"
    )

    manifest_rows = []
    for path in sorted(item for item in report.iterdir() if item.is_file()):
        manifest_rows.append(f"{sha256(path)}  {path.name}")
    (report / "MANIFEST.sha256").write_text("\n".join(manifest_rows) + "\n", encoding="utf-8")
    print(json.dumps({"status": "U0_GIT_ISOLATION_COMPLETE", "report": str(report), "worktree": str(worktree)}))


if __name__ == "__main__":
    main()
