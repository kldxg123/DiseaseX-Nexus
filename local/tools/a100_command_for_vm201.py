from __future__ import annotations

import sys
from pathlib import Path

from src.remote.server_config import _parse_positional_secret_file
from src.remote.ssh_runner import SSHRunner


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: python -m tools.a100_command_for_vm201 <remote-command>")
        return 2
    cfg_path = Path("服务器信息a100.txt")
    cfg = _parse_positional_secret_file(
        cfg_path,
        cfg_path.read_text(encoding="utf-8", errors="ignore"),
    )
    if cfg is None:
        print("A100_CONFIG_PARSE_FAILED")
        return 2
    result = SSHRunner(cfg, timeout=3600).run(sys.argv[1])
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.stderr:
        print(result.stderr, end="" if result.stderr.endswith("\n") else "\n")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
