from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any


PROJECT = Path(os.environ["PROJECT"])


def sha_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def safe(value: str) -> str:
    return re.sub(r"postgresql(?:\+\w+)?://[^\s'\"]+", "[DATABASE_URL_REDACTED]", value).replace(str(PROJECT), "[project]")


def run(command: str) -> dict[str, Any]:
    cp = subprocess.run(["bash", "-lc", command], cwd=PROJECT, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=60)
    out = safe((cp.stdout or "") + "\n" + (cp.stderr or ""))
    return {"returncode": cp.returncode, "output_sha256": sha_text(out), "lines": out.splitlines()[:120]}


def main() -> None:
    result = {
        "dashboard_listener": run("ss -ltnp 2>/dev/null | grep ':8020\\b' || true"),
        "dashboard_process": run("pid=$(ss -ltnp 2>/dev/null | sed -n -E 's/.*:8020 .*pid=([0-9]+).*/\\1/p' | head -1); if [ -n \"$pid\" ]; then ps -p \"$pid\" -o pid=,cmd=; fi"),
        "dashboard_scripts": run("find scripts -maxdepth 3 -type f | grep -E 'dashboard|source_review' | sort"),
        "dashboard_modules": run("find src/epidemic_core -maxdepth 3 -type f | grep -E 'dashboard.*\\.py$|app\\.py$' | sort"),
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
