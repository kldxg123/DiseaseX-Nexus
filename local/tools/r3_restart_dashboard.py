#!/usr/bin/env python3
from __future__ import annotations

import os
import signal
import subprocess
import time
import urllib.request
from pathlib import Path


def find_dashboard() -> tuple[int, str, list[str], dict[str, str]]:
    for p in os.listdir("/proc"):
        if not p.isdigit():
            continue
        try:
            cmd_raw = (Path("/proc") / p / "cmdline").read_bytes()
            if b"epidemic_core.dashboard.app" not in cmd_raw:
                continue
            cmd = [x.decode() for x in cmd_raw.split(b"\0") if x]
            cwd = os.readlink(Path("/proc") / p / "cwd")
            env = {}
            for item in (Path("/proc") / p / "environ").read_bytes().split(b"\0"):
                if b"=" in item:
                    k, v = item.split(b"=", 1)
                    env[k.decode()] = v.decode(errors="ignore")
            return int(p), cwd, cmd, env
        except Exception:
            continue
    raise RuntimeError("dashboard process not found")


def health() -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8020/healthz", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def main() -> int:
    old_pid, cwd, cmd, env = find_dashboard()
    log_dir = Path(cwd) / "logs"
    log_dir.mkdir(exist_ok=True)
    log = open(log_dir / "dashboard.r3-restart.log", "ab", buffering=0)
    os.kill(old_pid, signal.SIGTERM)
    for _ in range(40):
        try:
            os.kill(old_pid, 0)
            time.sleep(0.25)
        except ProcessLookupError:
            break
    proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=log, stderr=log, start_new_session=True)
    ok = False
    for _ in range(60):
        if health():
            ok = True
            break
        time.sleep(0.5)
    print({"old_pid": old_pid, "new_pid": proc.pid, "healthz": ok})
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
