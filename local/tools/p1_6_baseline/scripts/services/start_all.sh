#!/usr/bin/env bash
set -euo pipefail
ROOT=USER_HOME/autodl-tmp/epidemic-openclaw
"$ROOT/scripts/postgres/start.sh"
"$ROOT/scripts/qwen_vllm/start.sh"
"$ROOT/scripts/query_api/start_query_api.sh"
"$ROOT/scripts/retrieval_worker/start_retrieval_worker.sh"
"$ROOT/scripts/openclaw/start_openclaw.sh"
"$ROOT/scripts/dashboard/start_dashboard.sh"
