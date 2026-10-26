from __future__ import annotations

import argparse

from src.remote.p0b_utils import mirror_report, remote_project_prelude, write_local_report
from src.remote.server_config import ServerConfig
from src.remote.ssh_runner import SSHRunner


INIT_SCRIPT_TAIL = r"""
mkdir -p configs src scripts tests
mkdir -p data/raw data/interim data/processed data/outputs/reports data/outputs/figures
mkdir -p models lora_weights logs
if [ -d "$HOME/~/aibola" ] && [ "$PWD" != "$HOME/~/aibola" ]; then
  rm -rf "$HOME/~/aibola"
  rmdir "$HOME/~" 2>/dev/null || true
fi
printf 'remote_dir_ready\n'
printf 'pwd\n'
pwd || true
printf 'python3\n'
python3 --version || true
printf 'disk\n'
df -h . || true
printf 'memory\n'
free -h || true
printf 'nvidia\n'
nvidia-smi || true
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Initialize the remote ~/aibola project directory.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    cfg = ServerConfig.from_project_root(args.root)
    runner = SSHRunner(cfg, timeout=180, dry_run=args.dry_run)
    command = remote_project_prelude(cfg, create=True) + INIT_SCRIPT_TAIL
    result = runner.run(command, "logs/remote/init_remote_project_sanitized.log")
    status = "ok" if result.returncode == 0 else "blocked"
    blocked = "" if result.returncode == 0 else f"\nblocked_reason: remote_init_failed_returncode_{result.returncode}\n"
    body = (
        f"status: {status}\n"
        f"remote_dir: {cfg.remote_dir}\n"
        f"sanitized_command: {result.command}\n"
        f"{blocked}\n"
        "## Lightweight Probe\n\n"
        "```text\n"
        f"{(result.stdout or result.stderr).strip()}\n"
        "```\n"
    )
    local_report = write_local_report("remote_init_report.md", "Remote Init Report", body)
    if result.returncode == 0:
        mirror_code = mirror_report(runner, local_report, "remote_init_report.md")
        if mirror_code != 0:
            return mirror_code
    print(result.stdout or result.stderr)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
