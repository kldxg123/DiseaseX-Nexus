#!/usr/bin/env bash
set -euo pipefail
python -m src.eval.backtest --config configs/config.yaml --horizons 7 14 30
