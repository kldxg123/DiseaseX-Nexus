from __future__ import annotations

import argparse

from src.remote.p0b_utils import remote_project_prelude
from src.remote.server_config import ServerConfig
from src.remote.ssh_runner import SSHRunner


STOP_SCRIPT = r"""
set +e
PIDS=$(pgrep -f "python -m src.collectors.selected_external_dataset_downloader" 2>/dev/null)
if [ -z "$PIDS" ]; then
  echo "no_downloader_process"
  exit 0
fi
echo "stopping_pids:"
echo "$PIDS"
kill $PIDS 2>/dev/null
sleep 3
echo "remaining:"
pgrep -af "python -m src.collectors.selected_external_dataset_downloader" 2>/dev/null
exit 0
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Stop stalled remote selected external dataset downloader.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    cfg = ServerConfig.from_project_root(args.root)
    runner = SSHRunner(cfg, timeout=120, dry_run=args.dry_run)
    result = runner.run(remote_project_prelude(cfg) + STOP_SCRIPT)
    print(result.stdout or result.stderr)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
