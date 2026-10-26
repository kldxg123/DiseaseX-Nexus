from __future__ import annotations

import shlex

from src.remote.p0b_utils import remote_project_prelude
from src.remote.p0c1_runner import ACTIVATE_ENV
from src.remote.server_config import ServerConfig
from src.remote.ssh_runner import SSHRunner
from src.remote.p0c1_runner import parse_args


def main() -> int:
    args = parse_args("Launch P0c-4B QLoRA candidate-v2 training in remote tmux.")
    cfg = ServerConfig.from_project_root(args.root)
    command = (
        remote_project_prelude(cfg, create=True)
        + ACTIVATE_ENV.replace("__CONDA_ENV__", cfg.conda_env)
        + "set +e\n"
        + f"python -m src.remote.qwen_lora_v2_train_launcher --conda-env {shlex.quote(cfg.conda_env)} --verify-seconds 90\n"
    )
    result = SSHRunner(cfg, timeout=240, dry_run=args.dry_run).run(command, "logs/remote/train_lora_v2_launch_sanitized.log")
    print(result.stdout or result.stderr)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
