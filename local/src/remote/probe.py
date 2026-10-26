from __future__ import annotations

import argparse

from src.remote.p0b_utils import pull_report, remote_project_prelude, write_local_report
from src.remote.server_config import ServerConfig
from src.remote.ssh_runner import SSHRunner


PROBE_SCRIPT = r"""
mkdir -p data/outputs/reports logs
HF_STATUS="unknown"
HF_IMPACT="none"
if command -v python3 >/dev/null 2>&1; then
  HF_JSON=$(python3 - <<'PY'
import json
import os
import urllib.request

endpoint = os.environ.get("HF_ENDPOINT", "https://huggingface.co").rstrip("/")
try:
    with urllib.request.urlopen(endpoint, timeout=8) as response:
        ok = 200 <= response.status < 500
    print(json.dumps({"hf_reachable": bool(ok), "hf_endpoint_configured": bool(os.environ.get("HF_ENDPOINT")), "impact": "none" if ok else "P0c may be blocked"}))
except Exception as exc:
    print(json.dumps({"hf_reachable": False, "hf_endpoint_configured": bool(os.environ.get("HF_ENDPOINT")), "impact": "P0c may be blocked", "error_type": type(exc).__name__}))
PY
)
else
  HF_JSON='{"hf_reachable": false, "impact": "P0c may be blocked", "error_type": "python3_not_found"}'
fi

{
  echo "# Remote Probe Report"
  echo
  echo "status: ok"
  echo "remote_dir: ~/aibola"
  echo
  echo "## OS"
  uname -a || true
  echo
  echo "## Python"
  which python3 || true
  python3 --version || true
  echo
  echo "## Pip Conda"
  which pip || true
  pip --version || true
  which conda || true
  conda --version || true
  echo
  echo "## CUDA GPU"
  nvidia-smi || true
  nvcc --version || true
  echo
  echo "## Disk"
  df -h . || true
  echo
  echo "## Memory"
  free -h || true
  echo
  echo "## Network"
  python3 - <<'PY' || true
import urllib.request
for url in ["https://pypi.org", "https://huggingface.co"]:
    try:
        with urllib.request.urlopen(url, timeout=8) as response:
            print(f"{url}: reachable status={response.status}")
    except Exception as exc:
        print(f"{url}: unreachable error_type={type(exc).__name__}")
PY
  echo
  echo "## Hugging Face"
  echo "$HF_JSON"
} > data/outputs/reports/remote_probe_report.md

cat data/outputs/reports/remote_probe_report.md
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a sanitized remote probe.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--log", default="logs/remote/probe_sanitized.log")
    args = parser.parse_args()
    cfg = ServerConfig.from_project_root(args.root)
    runner = SSHRunner(cfg, timeout=120, dry_run=args.dry_run)
    result = runner.run(remote_project_prelude(cfg, create=True) + PROBE_SCRIPT, log_path=args.log)
    pull_code = pull_report(runner, "remote_probe_report.md")
    if pull_code != 0:
        write_local_report(
            "remote_probe_report.md",
            "Remote Probe Report",
            f"status: unknown\nblocked_reason: report_pull_failed\n\n```text\n{(result.stdout or result.stderr).strip()}\n```",
        )
    print(result.stdout or result.stderr)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
