#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
PROJECT=$(cd -- "$SCRIPT_DIR/../.." && pwd -P);RUNTIME="$PROJECT/../epidemic-source-review-runtime";PYTHON="$PROJECT/../conda/envs/epidemic-core/bin/python"
PIDFILE="$RUNTIME/run/review.pid";LOGFILE="$RUNTIME/log/review.log";CONFIG="$RUNTIME/secrets/review.env";AUTH_FILE="$RUNTIME/secrets/login_hash.json";HOST=127.0.0.1;PORT=8021
alive(){ [[ -f "$PIDFILE" ]]&&kill -0 "$(<"$PIDFILE")" 2>/dev/null; }
health(){ curl --silent --fail --noproxy '*' --max-time 5 "http://$HOST:$PORT/healthz" >/dev/null; }
