from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

roots = [Path("USER_HOME"), Path("USER_HOME")]
fastapi_dirs = []
for root in roots:
    if not root.exists():
        continue
    try:
        proc = subprocess.run(["find", str(root), "-maxdepth", "6", "-type", "d", "-name", "fastapi"], text=True, capture_output=True, timeout=20)
        fastapi_dirs.extend(proc.stdout.splitlines()[:20])
    except Exception as e:
        fastapi_dirs.append(f"ERR:{type(e).__name__}")

print(json.dumps({
    "psql": shutil.which("psql"),
    "pg_dump": shutil.which("pg_dump"),
    "python": sys.executable,
    "python_path": sys.path,
    "fastapi_dirs": fastapi_dirs[:30],
}, ensure_ascii=False, indent=2))
