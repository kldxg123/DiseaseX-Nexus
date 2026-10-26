#!/usr/bin/env bash
set -euo pipefail
python -m src.remote.detect_server
python -m src.remote.probe
python -m src.remote.bootstrap
python -m src.remote.sync
python -m src.remote.download_qwen
python -m src.remote.train_lora
python -m src.remote.start_api
