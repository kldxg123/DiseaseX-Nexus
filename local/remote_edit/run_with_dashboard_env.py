from __future__ import annotations

import os
import sys
from pathlib import Path

root = Path("USER_HOME/projects/openclaw")
pid_path = root / "runtime/dashboard.pid"
pid = pid_path.read_text(encoding="utf-8").strip()
raw = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
env: dict[str, str] = {}
for item in raw:
    if b"=" not in item:
        continue
    key, value = item.split(b"=", 1)
    env[key.decode()] = value.decode()
exe = os.readlink(f"/proc/{pid}/exe")
if len(sys.argv) < 2:
    raise SystemExit("usage: run_with_dashboard_env.py SCRIPT [ARGS...]")
os.execve(exe, [exe, *sys.argv[1:]], env)
