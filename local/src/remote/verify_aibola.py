from __future__ import annotations

import argparse
import json

from src.remote.server_config import ServerConfig
from src.remote.ssh_runner import SSHRunner


VERIFY_SCRIPT = r"""
set -eu
cd __REMOTE_DIR__
printf 'remote_pwd='
pwd
printf 'top_dirs='
find . -maxdepth 1 -type d | sed 's#^./##' | sort | tr '\n' ' '
printf '\n'
printf 'data_files='
find data -type f | wc -l
printf 'src_files='
find src -type f | wc -l
printf 'raw_files='
find data/raw -type f | wc -l
printf 'processed_files='
find data/processed -type f | wc -l
printf 'top_level_excluded='
find . -maxdepth 1 \( -name .git -o -name .codex -o -name .agents -o -name models -o -name lora_weights -o -name logs -o -name '*信息*' \) -print | wc -l
printf 'size_mb='
du -sm . | awk '{print $1}'
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify remote ~/aibola project sync without exposing secrets.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    cfg = ServerConfig.from_project_root(args.root)
    command = VERIFY_SCRIPT.replace("__REMOTE_DIR__", cfg.remote_dir)
    result = SSHRunner(cfg, timeout=120, dry_run=args.dry_run).run(command, "logs/remote/verify_aibola_sanitized.log")
    print(result.stdout or result.stderr)
    if result.returncode != 0:
        return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
