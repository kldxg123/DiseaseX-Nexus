from __future__ import annotations

import shlex
from pathlib import Path

from src.remote.server_config import ServerConfig
from src.remote.ssh_runner import SSHRunner
from src.utils.io import ensure_parent


LOCAL_REPORTS = Path("data/outputs/reports")
REMOTE_REPORTS = "data/outputs/reports"


def remote_project_prelude(cfg: ServerConfig, create: bool = False) -> str:
    mkdir = 'mkdir -p "$REMOTE_DIR"\n' if create else ""
    return (
        "set -eu\n"
        f"REMOTE_DIR={shlex.quote(cfg.remote_dir)}\n"
        'if [ "$REMOTE_DIR" = "~" ]; then\n'
        '  REMOTE_DIR="$HOME"\n'
        'elif [ "$(printf %s "$REMOTE_DIR" | cut -c1-2)" = "~/" ]; then\n'
        '  REMOTE_DIR="$HOME/$(printf %s "$REMOTE_DIR" | cut -c3-)"\n'
        "fi\n"
        f"{mkdir}"
        'cd "$REMOTE_DIR"\n'
    )


def write_local_report(name: str, title: str, body: str) -> Path:
    path = ensure_parent(LOCAL_REPORTS / name)
    path.write_text(f"# {title}\n\n{body.rstrip()}\n", encoding="utf-8")
    return path


def mirror_report(runner: SSHRunner, local_path: str | Path, remote_name: str) -> int:
    result = runner.sftp_put_file(local_path, f"{runner.config.remote_dir}/{REMOTE_REPORTS}/{remote_name}")
    return result.returncode


def pull_report(runner: SSHRunner, remote_name: str, local_name: str | None = None) -> int:
    local_path = LOCAL_REPORTS / (local_name or remote_name)
    result = runner.sftp_get_file(
        f"{runner.config.remote_dir}/{REMOTE_REPORTS}/{remote_name}",
        local_path,
    )
    if result.returncode != 0 and local_path.exists() and local_path.stat().st_size == 0:
        local_path.unlink()
    return result.returncode
