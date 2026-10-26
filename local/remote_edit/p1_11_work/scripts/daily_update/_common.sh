#!/usr/bin/env bash
set -euo pipefail
set +x
umask 077
PROJECT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)
source "$PROJECT/scripts/query_api/query_api_env.sh"
RUN_DIR="$PROJECT/storage/run/daily-update-coordinator"
LOG_DIR="$PROJECT/storage/logs/daily-update-coordinator"
PID_FILE="$RUN_DIR/worker.pid"
mkdir -p "$RUN_DIR" "$LOG_DIR"
chmod 0700 "$RUN_DIR" "$LOG_DIR"
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
