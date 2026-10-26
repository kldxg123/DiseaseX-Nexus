#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
PROJECT_ROOT=$(cd -- "$SCRIPT_DIR/../.." && pwd -P)
ROOT=$(cd -- "$PROJECT_ROOT/.." && pwd -P)
RUNTIME="$ROOT/epidemic-dashboard-runtime"
PYTHON="$ROOT/conda/envs/epidemic-core/bin/python"
PIDFILE="$RUNTIME/run/dashboard.pid"
LOGFILE="$RUNTIME/log/dashboard.log"
CONFIG="$RUNTIME/secrets/dashboard.env"
AUTH_FILE="$RUNTIME/secrets/login_hash.json"
HOST=127.0.0.1
PORT=8020

dashboard_alive() {
  [[ -r "$PIDFILE" ]] || return 1
  local pid
  pid=$(<"$PIDFILE")
  [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null
}

dashboard_health() {
  curl --silent --show-error --fail --noproxy '*' --connect-timeout 2 --max-time 5 \
    "http://$HOST:$PORT/healthz" >/dev/null
}

dashboard_ready() {
  curl --silent --show-error --fail --noproxy '*' --connect-timeout 2 --max-time 5 \
    "http://$HOST:$PORT/readyz" >/dev/null
}
