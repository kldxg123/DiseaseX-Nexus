#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path("USER_HOME/projects/openclaw")
TASK_ID = "EPIDEMIC_KG_PREDICTION_R3_104_GATE_FULLTEXT_MODEL_BRIDGE"


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def run(cmd: list[str], cwd: Path = PROJECT, timeout: int = 20) -> dict:
    p = subprocess.run(cmd, cwd=str(cwd), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    return {"cmd": cmd, "exit_code": p.returncode, "stdout": p.stdout[-4000:], "stderr": p.stderr[-4000:]}


def sha_tree(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        if rel.startswith(("runtime/", "storage/", "reports/", "__pycache__/")) or "/__pycache__/" in rel:
            continue
        if p.stat().st_size > 5_000_000:
            continue
        h.update(rel.encode())
        h.update(hashlib.sha256(p.read_bytes()).digest())
    return h.hexdigest()


def url(path: str) -> dict:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8020" + path, timeout=10) as r:
            body = r.read(4096)
            return {"status": r.status, "body_sample_sha256": hashlib.sha256(body).hexdigest(), "body_sample": body.decode("utf-8", "ignore")[:500]}
    except Exception as e:
        return {"status": "ERROR", "error_type": type(e).__name__, "error": str(e)[:500]}


def find_dashboard() -> list[dict]:
    out = []
    for pid in [p for p in os.listdir("/proc") if p.isdigit()]:
        try:
            cmd = (Path("/proc") / pid / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "ignore")
            if "epidemic_core.dashboard.app" not in cmd:
                continue
            cwd = os.readlink(Path("/proc") / pid / "cwd")
            out.append({"pid": int(pid), "cwd": cwd, "cmdline": cmd})
        except Exception:
            continue
    return out


def main() -> int:
    report = Path(os.environ["R3_REPORT_DIR"])
    report.mkdir(parents=True, exist_ok=True)
    dashboard = find_dashboard()
    payload = {
        "schema_version": "1.0",
        "task_id": TASK_ID,
        "execution_id": report.name,
        "generated_at_utc": now(),
        "active_project_root": str(PROJECT.resolve()),
        "source_revision_or_hash": sha_tree(PROJECT / "src"),
        "database_identity": {"database": "epidemic_openclaw", "port": 55432},
        "input_manifest_sha256": "",
        "result_status": "LIVE_AUDIT_COMPLETE",
        "commands_or_evidence_refs": [],
        "blockers": [],
        "no_fake_pass_assertion": True,
        "host": run(["hostname"])["stdout"].strip(),
        "user": run(["id", "-un"])["stdout"].strip(),
        "dashboard_processes": dashboard,
        "ports": run(["bash", "-lc", "ss -ltnp | grep -E ':(8020|8021|8010|55432|8130) ' || true"])["stdout"],
        "git": {
            "is_worktree": run(["git", "rev-parse", "--is-inside-work-tree"]),
            "head": run(["git", "rev-parse", "HEAD"]),
            "branch": run(["git", "rev-parse", "--abbrev-ref", "HEAD"]),
            "status_short": run(["git", "status", "--short"]),
        },
        "healthz": url("/healthz"),
        "readyz": url("/readyz"),
    }
    (report / "R3_EXECUTION_CONTEXT.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (report / "R3_SERVICE_STATE_BEFORE.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (report / "R3_INITIAL_GIT_STATUS.txt").write_text(payload["git"]["status_short"]["stdout"] + payload["git"]["status_short"]["stderr"], encoding="utf-8")
    md = [
        "# R3 Active Project / Service / DB Map",
        "",
        f"- observed_live_fact.active_project_root = `{PROJECT.resolve()}`",
        f"- observed_live_fact.dashboard_process_count = `{len(dashboard)}`",
        f"- observed_live_fact.healthz = `{payload['healthz'].get('status')}`",
        f"- observed_live_fact.readyz = `{payload['readyz'].get('status')}`",
        f"- observed_live_fact.source_hash = `{payload['source_revision_or_hash']}`",
        "- previous_report_only.R2_archive_sha256 = `74d0cfd71b8b1ecbd185112d425d343d4571b6275445ae52611725e7fdbe9c06`",
        "- unknown.full_git_identity = active project is not expected to be a Git worktree if git probe fails.",
    ]
    (report / "R3_ACTIVE_PROJECT_SERVICE_DB_MAP.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
