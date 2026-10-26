from __future__ import annotations

import json
import os

pid = None
for p in os.listdir("/proc"):
    if not p.isdigit():
        continue
    try:
        cmd = open(f"/proc/{p}/cmdline", "rb").read().replace(b"\0", b" ").decode("utf-8", "replace")
        cwd = os.readlink(f"/proc/{p}/cwd")
    except Exception:
        continue
    if "uvicorn" in cmd and "epidemic_core.dashboard.app:create_app" in cmd and cwd == "USER_HOME/projects/openclaw":
        pid = p
        break

if not pid:
    raise SystemExit("NO_DASHBOARD_PID")

keys = []
for item in open(f"/proc/{pid}/environ", "rb").read().split(b"\0"):
    if b"=" in item:
        keys.append(item.split(b"=", 1)[0].decode("utf-8", "replace"))

print(json.dumps({
    "pid": pid,
    "exe": os.readlink(f"/proc/{pid}/exe"),
    "cwd": os.readlink(f"/proc/{pid}/cwd"),
    "cmdline": open(f"/proc/{pid}/cmdline", "rb").read().replace(b"\0", b" ").decode("utf-8", "replace"),
    "env_keys": sorted(keys),
}, ensure_ascii=False, indent=2))
