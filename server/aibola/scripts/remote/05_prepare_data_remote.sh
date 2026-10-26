#!/usr/bin/env bash
set -euo pipefail
python -m src.collectors.ebola_case_loader --config configs/config.yaml || true
python -m src.collectors.openmeteo_collector --config configs/config.yaml || true
python -m src.processors.weather_aggregator --config configs/config.yaml || true
python -m src.processors.mobility_proxy_builder --config configs/config.yaml || true
python -m src.processors.feature_builder --config configs/config.yaml || true
python -m src.processors.label_builder --config configs/config.yaml --horizon-days 14 || true
