from __future__ import annotations

import argparse
from pathlib import Path

from src.remote.server_config import ServerConfig
from src.remote.ssh_runner import SSHRunner


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync project to remote with secret exclusions.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--include-data", action="store_true", help="Upload local MVP raw/processed data in addition to code.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    cfg = ServerConfig.from_project_root(args.root)
    result = SSHRunner(cfg, timeout=600, dry_run=args.dry_run).sftp_put_dir(
        Path(args.root),
        cfg.remote_dir,
        include_data=args.include_data,
    )
    print(result.stdout or result.stderr)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
