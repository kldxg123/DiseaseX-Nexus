from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import a100_p1_11 as p111


def main() -> None:
    action = sys.argv[1] if len(sys.argv) > 1 else "list"
    client, _gate = p111.p110r3.previous.p19.a100.connect()
    try:
        if action == "list-e2e":
            cmd = (
                "python3 - <<'PY'\n"
                "import subprocess, json\n"
                "out=subprocess.run(['pgrep','-af','/tmp/three_disease_e2e_remote.py'],text=True,capture_output=True)\n"
                "print(json.dumps({'matches':[line.split(None,1)[1] if len(line.split(None,1))>1 else line for line in out.stdout.splitlines()]}))\n"
                "PY"
            )
            rc, stdout, stderr = p111.p110r3.previous.p19.a100.run(client, cmd, timeout=60)
            if stdout:
                print(stdout)
            if rc:
                print(stderr, file=sys.stderr)
                raise SystemExit(rc)
        elif action == "kill-e2e":
            cmd = (
                "python3 - <<'PY'\n"
                "import os, signal, subprocess, json\n"
                "out=subprocess.run(['pgrep','-af','/tmp/three_disease_e2e_remote.py'],text=True,capture_output=True)\n"
                "killed=[]\n"
                "for line in out.stdout.splitlines():\n"
                "    pid=int(line.split()[0])\n"
                "    if pid != os.getpid():\n"
                "        os.kill(pid, signal.SIGTERM)\n"
                "        killed.append(pid)\n"
                "print(json.dumps({'killed_count':len(killed)}))\n"
                "PY"
            )
            rc, stdout, stderr = p111.p110r3.previous.p19.a100.run(client, cmd, timeout=60)
            if stdout:
                print(stdout)
            if rc:
                print(stderr, file=sys.stderr)
                raise SystemExit(rc)
        else:
            raise SystemExit(f"unknown action: {action}")
    finally:
        client.close()


if __name__ == "__main__":
    main()
