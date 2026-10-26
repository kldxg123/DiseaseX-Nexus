from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path


PROJECT = Path(os.environ.get("PROJECT", ".")).resolve()


def run(label: str, args: list[str], timeout: int = 30) -> dict[str, object]:
    started = datetime.now(timezone.utc).isoformat()
    try:
        proc = subprocess.run(args, cwd=PROJECT, text=True, capture_output=True, timeout=timeout)
        return {
            "label": label,
            "argv": args,
            "started_at_utc": started,
            "finished_at_utc": datetime.now(timezone.utc).isoformat(),
            "returncode": proc.returncode,
            "stdout": proc.stdout[-12000:],
            "stderr": proc.stderr[-4000:],
        }
    except Exception as exc:
        return {
            "label": label,
            "argv": args,
            "started_at_utc": started,
            "finished_at_utc": datetime.now(timezone.utc).isoformat(),
            "error_type": type(exc).__name__,
            "error": str(exc),
        }


def main() -> None:
    checks = [
        ("pwd", ["bash", "-lc", "pwd -P"]),
        ("git_head_dirty", ["bash", "-lc", "git rev-parse HEAD; git status --short | wc -l"]),
        ("project_ports", ["bash", "-lc", "ss -ltnp 2>/dev/null | grep -E ':(8020|8021|8022|8023|18020|18021)\\b' || true"]),
        ("project_pids", ["bash", "-lc", "for f in ../epidemic-dashboard-runtime/run/dashboard.pid ../epidemic-source-review-runtime/run/review.pid storage/run/source-discovery-worker/worker.pid storage/run/preview-publication-worker/worker.pid storage/run/daily-update/worker.pid; do if [ -f \"$f\" ]; then printf '%s=' \"$f\"; cat \"$f\"; printf '\\n'; fi; done"]),
        ("project_processes", ["bash", "-lc", "ps -eo pid,ppid,stat,etime,pcpu,pmem,comm,args --sort=-pcpu | grep -E 'uvicorn|dashboard|source_review|source-discovery|preview-publication|daily_update|openclaw|vllm|qwen|python' | grep -v grep | head -40"]),
        ("system_load", ["bash", "-lc", "uptime; nproc; free -h; df -h / /home /root USER_HOME/autodl-tmp 2>/dev/null || true"]),
        ("top_cpu", ["bash", "-lc", "ps -eo pid,ppid,stat,etime,pcpu,pmem,comm,args --sort=-pcpu | head -25"]),
        ("gpu_summary", ["bash", "-lc", "if command -v nvidia-smi >/dev/null 2>&1; then nvidia-smi --query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw --format=csv,noheader,nounits; else echo NO_NVIDIA_SMI; fi"]),
        ("gpu_processes", ["bash", "-lc", "if command -v nvidia-smi >/dev/null 2>&1; then nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader,nounits 2>/dev/null || true; fi"]),
        ("postgres_activity_count", ["bash", "-lc", "source scripts/database/_env.sh >/dev/null 2>&1; set -a; source .secrets/postgres.env >/dev/null 2>&1; set +a; python - <<'PY'\nimport json, os\nimport sqlalchemy as sa\nfrom sqlalchemy import text\nfrom sqlalchemy.engine import make_url\nraw=os.environ.get('MIGRATION_DATABASE_URL') or os.environ.get('DATABASE_URL') or os.environ.get('DASHBOARD_DATABASE_URL')\nurl=make_url(raw)\nif os.environ.get('POSTGRES_ADMIN_USER') and os.environ.get('POSTGRES_ADMIN_PASSWORD'):\n    url=url.set(username=os.environ['POSTGRES_ADMIN_USER'], password=os.environ['POSTGRES_ADMIN_PASSWORD'])\nengine=sa.create_engine(url, hide_parameters=True, future=True)\nwith engine.connect() as conn:\n    rows=[dict(r) for r in conn.execute(text(\"\"\"\n        SELECT state, wait_event_type, count(*)::int AS n\n        FROM pg_stat_activity\n        WHERE datname=current_database()\n        GROUP BY state, wait_event_type\n        ORDER BY n DESC, state NULLS LAST\n    \"\"\")).mappings()]\n    long_running=[dict(r) for r in conn.execute(text(\"\"\"\n        SELECT state, wait_event_type, now()-query_start AS age, left(query,80) AS query_prefix\n        FROM pg_stat_activity\n        WHERE datname=current_database() AND pid<>pg_backend_pid() AND query_start IS NOT NULL\n        ORDER BY query_start ASC LIMIT 8\n    \"\"\")).mappings()]\nprint(json.dumps({'activity':rows,'long_running':long_running}, default=str, ensure_ascii=False))\nPY"], 60),
    ]
    results = {}
    for item in checks:
        if len(item) == 3:
            label, args, timeout = item  # type: ignore[misc]
        else:
            label, args = item  # type: ignore[misc]
            timeout = 30
        results[str(label)] = run(str(label), list(args), timeout=int(timeout))

    # Derive a compact risk assessment without exposing sensitive command details.
    ports_open = bool(results["project_ports"].get("stdout", "").strip())
    gpu_out = str(results["gpu_summary"].get("stdout", ""))
    gpu_procs = str(results["gpu_processes"].get("stdout", "")).strip()
    project_processes = str(results["project_processes"].get("stdout", "")).strip()
    load_out = str(results["system_load"].get("stdout", ""))
    risk = "LOW"
    reasons: list[str] = []
    if ports_open or project_processes:
        risk = "MEDIUM"
        reasons.append("project-related processes or ports are already present")
    if gpu_procs:
        risk = "MEDIUM"
        reasons.append("GPU compute process exists")
    if "NO_NVIDIA_SMI" not in gpu_out:
        for line in gpu_out.splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 5:
                try:
                    util = float(parts[2])
                    used = float(parts[3])
                    total = float(parts[4])
                    if util >= 40 or (total and used / total >= 0.60):
                        risk = "HIGH"
                        reasons.append("GPU utilization or memory usage is high")
                except ValueError:
                    pass
    if not reasons:
        reasons.append("dashboard/review startup is light and no project service appears active")
    print(json.dumps({"probe_at_utc": datetime.now(timezone.utc).isoformat(), "project": str(PROJECT), "results": results, "competition_risk_if_start_dashboard_only": risk, "risk_reasons": reasons}, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
