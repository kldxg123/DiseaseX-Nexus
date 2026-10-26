from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.remote.p0b_utils import remote_project_prelude, write_local_report
from src.remote.server_config import ServerConfig
from src.remote.ssh_runner import SSHRunner


STATUS_SCRIPT = r"""
set +e
echo "REPORTS"
ls -l data/outputs/reports/external_dataset_download_report.* 2>/dev/null
echo "RAW_SUMMARY"
if [ -d data/raw/external_selected ]; then
  du -sh data/raw/external_selected 2>/dev/null
  find data/raw/external_selected -type f -printf '%s %p\n' 2>/dev/null | sort -nr | head -80
else
  echo "no_external_selected_dir"
fi
echo "TMP_FILES"
find data/raw/external_selected -name '*.tmp' -printf '%s %p\n' 2>/dev/null
echo "PROCESS"
pgrep -af "python -m src.collectors.selected_external_dataset_downloader" 2>/dev/null
PIDS=$(pgrep -f "python -m src.collectors.selected_external_dataset_downloader" 2>/dev/null | tr '\n' ',' | sed 's/,$//')
if [ -n "$PIDS" ]; then
  ps -o pid,ppid,etime,stat,pcpu,pmem,rss,wchan,args -p "$PIDS" 2>/dev/null
fi
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Check remote selected external dataset downloader status.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    cfg = ServerConfig.from_project_root(args.root)
    runner = SSHRunner(cfg, timeout=180, dry_run=args.dry_run)
    result = runner.run(remote_project_prelude(cfg) + STATUS_SCRIPT)
    payload = {
        "returncode": result.returncode,
        "status_output": result.stdout or result.stderr,
        "blocked_reason": "none" if result.returncode == 0 else f"status_check_returncode_{result.returncode}",
    }
    local_report = write_local_report(
        "external_dataset_download_status.md",
        "External Dataset Download Status",
        "```text\n" + payload["status_output"].strip() + "\n```\n",
    )
    Path("data/outputs/reports/external_dataset_download_status.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(payload["status_output"])
    print(f"local_status_report={local_report.as_posix()}")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
