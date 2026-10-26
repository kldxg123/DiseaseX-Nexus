from __future__ import annotations

import json
import os
from pathlib import Path


def main() -> None:
    project = Path(os.environ["PROJECT"])
    reports = sorted((project / "artifacts").glob("epidemic_fasttrack_correctness_repair_*"), key=lambda p: p.stat().st_mtime)
    latest = reports[-1]
    base = latest / "commands"
    payload = {"latest": latest.name, "files": {}}
    for name in [
        "alembic_upgrade_head.stderr.txt",
        "alembic_upgrade_head.stdout.txt",
        "alembic_heads.stdout.txt",
        "alembic_current_before.stdout.txt",
        "alembic_current_after.stdout.txt",
    ]:
        path = base / name
        payload["files"][name] = path.read_text(encoding="utf-8", errors="replace")[:4000] if path.exists() else None
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
