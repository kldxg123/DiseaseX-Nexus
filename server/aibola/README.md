# Ebola STEP Quantitative Trend MVP

This repository is the first-stage MVP of a STEP public-health sentinel framework applied to Ebola quantitative trend prediction. It does not implement the broader OSINT, misinformation, nanopore quick-test, port surveillance, or official public-health validation workflows.

The MVP predicts whether future Ebola **new cases** over 7, 14, or 30 days are `上升`, `下降`, or `稳定` from structured quantitative data:

- Case time series.
- Weather and ecological variables.
- Traffic and population-mobility proxy variables.

`下降` means a decrease in future new-case volume or cumulative-case growth rate. It never means cumulative cases themselves decrease.

## Gates

Implementation proceeds in order:

1. `P0a`: local MVP, smoke test, data-quality gate, API, tests.
2. `P0b`: sanitized remote detection/probe/bootstrap/sync/download/start CLIs.
3. `P0c`: Qwen/Qwen2.5-7B-Instruct download, hardware/data gates, and conditional QLoRA.

P1/P2 modules such as real WorldPop/OSM/GTMD/WorldBank connectors, OpenSky, NDVI/EVI, continuous STEP, and adapter promotion/rollback are not expanded before P0 gates pass.

## Remote-First Workspace

The local machine is only a control client. It may read the server-info file, read the existing local MVP data bundle, upload required code/data through SSH/SFTP, trigger remote commands, and pull generated reports.

The official project workspace is the remote `aibola` directory. If the server-info file explicitly provides `remote_dir`, the effective project directory is `remote_dir/aibola`; otherwise it is `~/aibola`. Do not download Qwen weights, train LoRA adapters, run FastAPI, or store remote training artifacts in the local project root.

## Data Integrity

Daily or weekly case data is required for real 7/14/30-day training. Annual-only case data is treated as background and blocks short-horizon model training. When sufficient real data is unavailable, the project writes `data/outputs/reports/data_quality_report.md` and runs only the explicitly synthetic smoke test.

Synthetic smoke-test reports are marked synthetic and must not be presented as real model performance.

## Variables

Observed data may include case counts, weather measurements/reanalysis, and some transport indicators. Mobility pressure fields such as `traffic_flow_index`, `mobility_pressure_index`, and `infectious_inflow_index` are proxies or model estimates, not global real daily road traffic. OpenSky flight counts, when later enabled, are flight counts, not passenger counts. Weather/ecology variables must not be interpreted as direct causal drivers of Ebola transmission.

## Qwen Model

The default and only P0c base model is:

`Qwen/Qwen2.5-7B-Instruct`

Do not use VL, Omni, GGUF, 1M, or other variants unless explicitly configured. The base model is never overwritten; only LoRA adapters are candidates for update. `allow_auto_promote` defaults to `false`.

## Commands

Local smoke test:

```bash
python scripts/local_smoke_test.py
```

Compile:

```bash
python -m compileall src scripts tests
```

Tests:

```bash
pytest -q
```

If pytest is not installed in the local Python, run the bundled fallback:

```bash
python scripts/run_test_functions.py
```

Remote CLIs:

```bash
python -m src.remote.detect_server
python -m src.remote.probe --dry-run
python -m src.remote.bootstrap --dry-run
python -m src.remote.sync --dry-run
python -m src.remote.download_qwen --dry-run
python -m src.remote.train_lora --dry-run
python -m src.remote.start_api --dry-run
```

API:

```bash
uvicorn src.api.server:app --host 0.0.0.0 --port 8000
```

`/predict_trend` reads the local feature store by default. External fetching is disabled by default; if enabled, it must be cached, timeout-limited, and return `data_quality_flags` on failure.

## Security

Server files, `.env`, private keys, tokens, raw data, processed data, logs, models, and LoRA weights are ignored. The existing `服务器信息.txt` is treated as secret material and is never printed or copied into logs, README, code, reports, or command lines.

This system is for research support only and does not replace public-health expert judgment.
