#!/usr/bin/env python3
"""Restart dashboard with env restored from /tmp/dash_env_snapshot.txt (no secrets printed)."""
import os, subprocess, sys, time, urllib.request

ROOT = "USER_HOME/projects/openclaw"
SNAP = "/tmp/dash_env_snapshot.txt"
LOG = os.path.join(ROOT, "runtime/logs/dashboard_r3c_restart.log")
PIDF = os.path.join(ROOT, "runtime/dashboard.pid")

env = {}
for line in open(SNAP, encoding="utf-8"):
    line = line.rstrip("\n")
    if "=" in line:
        k, v = line.split("=", 1)
        env[k] = v
assert "DASHBOARD_DATABASE_URL" in env and "PYTHONPATH" in env, "snapshot incomplete"

old = open(PIDF).read().strip() if os.path.exists(PIDF) else ""
if old:
    subprocess.run(["kill", old], capture_output=True)
    time.sleep(2)

log = open(LOG, "ab")
p = subprocess.Popen(["/usr/bin/python3.10", "-m", "uvicorn",
                      "epidemic_core.dashboard.app:create_app", "--factory",
                      "--host", "127.0.0.1", "--port", "8020"],
                     env=env, cwd=ROOT, stdout=log, stderr=log,
                     start_new_session=True)
open(PIDF, "w").write(str(p.pid))
for _ in range(40):
    time.sleep(1)
    try:
        code = urllib.request.urlopen("http://127.0.0.1:8020/healthz", timeout=3).status
        if code == 200:
            break
    except Exception:
        code = 0
print("pid:", p.pid, "healthz:", code)
ready = 0
try:
    ready = urllib.request.urlopen("http://127.0.0.1:8020/readyz", timeout=5).status
except Exception:
    pass
print("readyz:", ready)
