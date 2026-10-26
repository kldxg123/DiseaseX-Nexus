from __future__ import annotations

import argparse

from src.remote.server_config import ServerConfig
from src.remote.ssh_runner import SSHRunner


def main() -> int:
    parser = argparse.ArgumentParser(description="Check remote API tmux session and local health endpoint.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    cfg = ServerConfig.from_project_root(args.root)
    command = f"""
set -eu
cd {cfg.remote_dir}
echo "api:tmux"
tmux ls 2>/dev/null | grep ebola_step_api || true
echo "api:health"
curl -fsS --max-time 5 http://127.0.0.1:8000/health || true
echo
echo "api:log_tail"
tail -n 20 logs/api.log 2>/dev/null || true
"""
    result = SSHRunner(cfg, timeout=60, dry_run=args.dry_run).run(command, "logs/remote/api_status_sanitized.log")
    print(result.stdout or result.stderr)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
