#!/usr/bin/env bash
set -euo pipefail
PROJECT_ROOT=USER_HOME/autodl-tmp/epidemic-openclaw
RUNTIME=USER_HOME/autodl-tmp/epidemic-dashboard-runtime
PYTHON=USER_HOME/autodl-tmp/conda/envs/epidemic-core/bin/python
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
