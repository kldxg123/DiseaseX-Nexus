from __future__ import annotations

import argparse

from src.remote.server_config import ServerConfig
from src.remote.ssh_runner import SSHRunner


def main() -> int:
    parser = argparse.ArgumentParser(description="Start remote FastAPI in tmux.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    cfg = ServerConfig.from_project_root(args.root)
    command = (
        f"cd {cfg.remote_dir} && mkdir -p logs && "
        "tmux new -d -s ebola_step_api "
        f"'source $HOME/miniconda3/etc/profile.d/conda.sh && conda run -n {cfg.conda_env} uvicorn src.api.server:app --host 0.0.0.0 --port 8000 2>&1 | tee logs/api.log'"
    )
    result = SSHRunner(cfg, timeout=120, dry_run=args.dry_run).run(command, "logs/remote/start_api_sanitized.log")
    print(result.stdout or result.stderr)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
