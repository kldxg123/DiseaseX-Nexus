from __future__ import annotations

import argparse
import shlex

from src.remote.p0b_utils import remote_project_prelude
from src.remote.p0c1_runner import ACTIVATE_ENV
from src.remote.server_config import ServerConfig
from src.remote.ssh_runner import SSHRunner


def main() -> int:
    parser = argparse.ArgumentParser(description="Launch P0c-2 QLoRA training in remote tmux.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    cfg = ServerConfig.from_project_root(args.root)
    command = (
        remote_project_prelude(cfg, create=True)
        + ACTIVATE_ENV.replace("__CONDA_ENV__", cfg.conda_env)
        + "set +e\n"
        + f"python -m src.remote.qwen_lora_train_launcher --config configs/train_qwen25_lora.yaml --conda-env {shlex.quote(cfg.conda_env)} --verify-seconds 90\n"
    )
    result = SSHRunner(cfg, timeout=240, dry_run=args.dry_run).run(command, "logs/remote/train_lora_launch_sanitized.log")
    print(result.stdout or result.stderr)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
