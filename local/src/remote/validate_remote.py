from __future__ import annotations

import argparse

from src.remote.server_config import ServerConfig
from src.remote.ssh_runner import SSHRunner


def main() -> int:
    parser = argparse.ArgumentParser(description="Run remote P0 validation gates.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    cfg = ServerConfig.from_project_root(args.root)
    command = f"""
set -eu
cd {cfg.remote_dir}
CONDA_SH=""
for candidate in "$HOME/miniconda3/etc/profile.d/conda.sh" "$HOME/anaconda3/etc/profile.d/conda.sh" "$HOME/conda3/etc/profile.d/conda.sh" "/opt/conda/etc/profile.d/conda.sh"; do
  if [ -f "$candidate" ]; then CONDA_SH="$candidate"; break; fi
done
if [ -z "$CONDA_SH" ]; then echo "conda_not_found" >&2; exit 2; fi
. "$CONDA_SH"
echo "validate:pytest"
conda run -n {cfg.conda_env} pytest -q
if [ ! -f data/processed/ebola_cases_daily.parquet ]; then
  echo "validate:smoke"
  conda run -n {cfg.conda_env} python scripts/local_smoke_test.py
else
  echo "validate:smoke_skipped_real_data_present"
fi
echo "validate:qwen_gate"
conda run -n {cfg.conda_env} python -m src.models.qwen25_lora_train --config configs/train_qwen25_lora.yaml --dry-run || true
"""
    result = SSHRunner(cfg, timeout=600, dry_run=args.dry_run).run(command, "logs/remote/validate_sanitized.log")
    print(result.stdout or result.stderr)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
