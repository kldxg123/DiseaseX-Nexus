from __future__ import annotations

import argparse

from src.remote.server_config import ServerConfig
from src.remote.ssh_runner import SSHRunner


def main() -> int:
    parser = argparse.ArgumentParser(description="Run remote non-GPU data preparation.")
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
conda run -n {cfg.conda_env} python -m src.processors.prepare_data --config configs/config.yaml || true
conda run -n {cfg.conda_env} python -m src.collectors.openmeteo_collector --config configs/config.yaml --points-jsonl data/processed/weather_points.jsonl || true
conda run -n {cfg.conda_env} python -m src.processors.weather_aggregator --config configs/config.yaml || true
conda run -n {cfg.conda_env} python -m src.processors.feature_builder --config configs/config.yaml || true
conda run -n {cfg.conda_env} python -m src.processors.label_builder --config configs/config.yaml --horizon-days 7 --target labels || true
conda run -n {cfg.conda_env} python -m src.processors.label_builder --config configs/config.yaml --horizon-days 14 --target labels || true
conda run -n {cfg.conda_env} python -m src.processors.label_builder --config configs/config.yaml --horizon-days 30 --target qwen25 || true
conda run -n {cfg.conda_env} python -m src.models.baseline_lightgbm --config configs/config.yaml --horizon-days 14 || true
conda run -n {cfg.conda_env} python -m src.eval.backtest --config configs/config.yaml --horizons 7 14 30 || true
"""
    result = SSHRunner(cfg, timeout=1800, dry_run=args.dry_run).run(command, "logs/remote/prepare_data_sanitized.log")
    print(result.stdout or result.stderr)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
