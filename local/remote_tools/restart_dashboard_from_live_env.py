from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


PROJECT = Path("USER_HOME/projects/openclaw")
PIDFILE = PROJECT / "runtime/dashboard.pid"
LOGFILE = PROJECT / "runtime/dashboard.log"


def read_process_environment(pid: int) -> dict[str, str]:
    payload = Path(f"/proc/{pid}/environ").read_bytes()
    environment: dict[str, str] = {}
    for item in payload.split(b"\0"):
        if not item or b"=" not in item:
            continue
        key, value = item.split(b"=", 1)
        environment[key.decode("utf-8")] = value.decode("utf-8")
    return environment


def ready() -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8020/readyz", timeout=3) as response:
            return response.status == 200
    except Exception:
        return False


def main() -> None:
    old_pid = int(PIDFILE.read_text(encoding="utf-8").strip())
    environment = read_process_environment(old_pid)
    required = {"PYTHONPATH", "DASHBOARD_DATABASE_URL", "DASHBOARD_AUTH_FILE"}
    missing = sorted(required - environment.keys())
    if missing:
        raise RuntimeError(f"live process environment is missing required key names: {missing}")
    executable = os.readlink(f"/proc/{old_pid}/exe")
    command_bytes = Path(f"/proc/{old_pid}/cmdline").read_bytes().split(b"\0")
    command = [item.decode("utf-8") for item in command_bytes if item]
    if not command or "uvicorn" not in " ".join(command):
        raise RuntimeError("live PID is not the expected dashboard process")
    command[0] = executable

    os.kill(old_pid, signal.SIGTERM)
    for _ in range(100):
        try:
            os.kill(old_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        raise RuntimeError("old dashboard did not terminate")

    with LOGFILE.open("ab", buffering=0) as log:
        process = subprocess.Popen(
            command,
            cwd=PROJECT,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
    PIDFILE.write_text(f"{process.pid}\n", encoding="utf-8")
    os.chmod(PIDFILE, 0o600)
    for _ in range(100):
        if process.poll() is not None:
            raise RuntimeError("new dashboard exited before readiness")
        if ready():
            print("dashboard_restart=PASS_REDACTED_ENV")
            print(f"new_pid={process.pid}")
            return
        time.sleep(0.2)
    raise RuntimeError("new dashboard did not become ready")


if __name__ == "__main__":
    main()
