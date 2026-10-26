from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


PROJECT = Path(".").resolve()


def run(label: str, args: list[str]) -> dict[str, object]:
    started = datetime.now(timezone.utc).isoformat()
    proc = subprocess.run(args, cwd=PROJECT, text=True, capture_output=True, timeout=120)
    return {
        "label": label,
        "args": args,
        "started_at_utc": started,
        "finished_at_utc": datetime.now(timezone.utc).isoformat(),
        "returncode": proc.returncode,
        "stdout_tail": proc.stdout[-2000:],
        "stderr_tail": proc.stderr[-2000:],
    }


def main() -> None:
    candidates = [
        ("dashboard", "scripts/dashboard/stop_dashboard.sh"),
        ("source_review", "scripts/source_review/stop_source_review.sh"),
        ("source_discovery_worker", "scripts/source_discovery_worker/stop_source_discovery_worker.sh"),
        ("preview_publication_worker", "scripts/preview_publication_worker/stop_preview_publication_worker.sh"),
        ("daily_update", "scripts/daily_update/stop_daily_update.sh"),
    ]
    results = []
    for label, rel in candidates:
        path = PROJECT / rel
        if path.exists():
            results.append(run(label, ["bash", rel]))
        else:
            results.append({"label": label, "script": rel, "skipped": "missing"})
    listen = run("listening_after", ["bash", "-lc", "ss -ltnp 2>/dev/null | grep -E ':(8020|8021|8022|8023)\\b' || true"])
    results.append(listen)
    print(json.dumps({"stopped_project_services": results}, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
