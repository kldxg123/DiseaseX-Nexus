#!/usr/bin/env bash
set -euo pipefail
ROOT=USER_HOME/autodl-tmp/epidemic-openclaw
"$ROOT/scripts/postgres/start.sh"
"$ROOT/scripts/qwen_vllm/start.sh"
"$ROOT/scripts/query_api/start_query_api.sh"
"$ROOT/scripts/retrieval_worker/start_retrieval_worker.sh"
"$ROOT/scripts/daily_update/start_daily_update.sh"
"$ROOT/scripts/openclaw/start_openclaw.sh"
"$ROOT/scripts/daily_update/install_openclaw_cron.sh" >/dev/null
"$ROOT/scripts/daily_update/reconcile_due.sh" --trigger startup >/dev/null
"$ROOT/scripts/dashboard/start_dashboard.sh"
"$ROOT/scripts/source_discovery_worker/start_source_discovery_worker.sh"
"$ROOT/scripts/source_promotion_worker/start_source_promotion_worker.sh"
"$ROOT/scripts/source_review/start_source_review.sh"
