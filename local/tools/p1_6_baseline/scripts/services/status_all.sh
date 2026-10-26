#!/usr/bin/env bash
set -euo pipefail
ROOT=USER_HOME/autodl-tmp/epidemic-openclaw
"$ROOT/scripts/postgres/status.sh"
"$ROOT/scripts/qwen_vllm/status.sh"
"$ROOT/scripts/query_api/status_query_api.sh"
"$ROOT/scripts/retrieval_worker/status_retrieval_worker.sh"
"$ROOT/scripts/openclaw/status_openclaw.sh"
"$ROOT/scripts/dashboard/status_dashboard.sh"
