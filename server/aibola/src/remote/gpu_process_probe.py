from __future__ import annotations

import argparse
import csv
import json
from io import StringIO
from pathlib import Path

from src.remote.p0b_utils import remote_project_prelude
from src.remote.server_config import ServerConfig
from src.remote.ssh_runner import SSHRunner


REMOTE_SCRIPT = r"""
import csv
import json
import subprocess
from io import StringIO
from pathlib import Path


def run(args):
    return subprocess.run(args, capture_output=True, text=True, check=False, timeout=30)


gpu_query = run([
    "nvidia-smi",
    "--query-gpu=index,uuid,name,memory.total,memory.used,utilization.gpu",
    "--format=csv,noheader,nounits",
])
proc_query = run([
    "nvidia-smi",
    "--query-compute-apps=gpu_uuid,pid,process_name,used_memory",
    "--format=csv,noheader,nounits",
])

gpus = []
uuid_to_index = {}
if gpu_query.returncode == 0:
    for row in csv.reader(StringIO(gpu_query.stdout)):
        if len(row) >= 6:
            index = int(float(row[0].strip()))
            uuid = row[1].strip()
            uuid_to_index[uuid] = index
            gpus.append({
                "index": index,
                "uuid": uuid,
                "name": row[2].strip(),
                "memory_total_mb": int(float(row[3].strip())),
                "memory_used_mb": int(float(row[4].strip())),
                "utilization_gpu_pct": int(float(row[5].strip())),
                "processes": [],
            })

processes = []
if proc_query.returncode == 0 and proc_query.stdout.strip():
    for row in csv.reader(StringIO(proc_query.stdout)):
        if len(row) >= 4:
            uuid = row[0].strip()
            process = {
                "gpu_index": uuid_to_index.get(uuid, -1),
                "pid": row[1].strip(),
                "process_name": row[2].strip().replace(str(Path.home()), "$HOME"),
                "used_memory_mb": int(float(row[3].strip())),
            }
            processes.append(process)
            for gpu in gpus:
                if gpu["index"] == process["gpu_index"]:
                    gpu["processes"].append(process)

ps_details = {}
if processes:
    pid_list = ",".join(sorted({process["pid"] for process in processes}))
    ps_query = run(["ps", "-o", "pid=,user=,etime=,cmd=", "-p", pid_list])
    if ps_query.returncode == 0:
        for line in ps_query.stdout.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            pid = stripped.split(maxsplit=1)[0]
            ps_details[pid] = stripped.replace(str(Path.home()), "$HOME")

print(json.dumps({
    "status": "ok" if gpu_query.returncode == 0 else "blocked",
    "blocked_reason": "none" if gpu_query.returncode == 0 else "nvidia_smi_failed",
    "process_count": len(processes),
    "gpus": gpus,
    "processes": processes,
    "ps_details": ps_details,
}, ensure_ascii=False, indent=2))
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe remote GPU process memory usage.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    cfg = ServerConfig.from_project_root(args.root)
    runner = SSHRunner(cfg, timeout=120, dry_run=args.dry_run)
    command = remote_project_prelude(cfg, create=False) + "python3 - <<'PY'\n" + REMOTE_SCRIPT + "\nPY\n"
    result = runner.run(command, "logs/remote/gpu_process_probe_sanitized.log")
    print(result.stdout or result.stderr)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
