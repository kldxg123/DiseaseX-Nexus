#!/usr/bin/env bash
set -euo pipefail
ROOT=USER_HOME/autodl-tmp/epidemic-openclaw
"$ROOT/scripts/dashboard/stop_dashboard.sh"
"$ROOT/scripts/openclaw/stop_openclaw.sh"
"$ROOT/scripts/retrieval_worker/stop_retrieval_worker.sh"
"$ROOT/scripts/query_api/stop_query_api.sh"
"$ROOT/scripts/qwen_vllm/stop.sh"
"$ROOT/scripts/postgres/stop.sh"
