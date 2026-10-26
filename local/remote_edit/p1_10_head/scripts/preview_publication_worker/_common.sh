#!/usr/bin/env bash
set -euo pipefail
set +x
umask 077
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
PROJECT=$(cd -- "$SCRIPT_DIR/../.." && pwd -P)
ROOT=$(cd -- "$PROJECT/.." && pwd -P)
PYTHON="$ROOT/conda/envs/epidemic-core/bin/python"
RUN="$PROJECT/storage/run/preview-publication-worker"
LOG="$PROJECT/storage/logs/preview-publication-worker"
PIDFILE="$RUN/worker.pid"
CONFIG="$PROJECT/.secrets/preview.env"
mkdir -p "$RUN" "$LOG"
chmod 0700 "$RUN" "$LOG"

alive() {
  [[ -r "$PIDFILE" ]] || return 1
  local pid
  pid=$(<"$PIDFILE")
  [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null
}

load_preview_environment() {
  [[ -r "$CONFIG" ]] || { echo 'preview worker configuration unavailable' >&2; return 1; }
  set -a
  source "$CONFIG"
  set +a
  export APP_ENV=production
  export DISCOVERY_QUARANTINE_ROOT="$PROJECT/storage/quarantine/sha256"
  export PYTHONPATH="$PROJECT/src"
  unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY no_proxy NO_PROXY GIT_CONFIG
  unset POSTGRES_ADMIN_PASSWORD POSTGRES_APP_PASSWORD EPIDEMIC_QUERY_API_TOKEN OPENCLAW_GATEWAY_TOKEN VLLM_API_KEY
}
