#!/usr/bin/env bash
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
source "$SCRIPT_DIR/_common.sh"
if alive;then
  if ready;then exit 0;fi
  kill "$(<"$PIDFILE")" 2>/dev/null || true
  for _ in $(seq 1 30);do alive||break;sleep 1;done
  if alive;then exit 1;fi
  rm -f "$PIDFILE"
fi
install -d -m 0700 "$RUNTIME/run" "$RUNTIME/log"
set -a;source "$CONFIG";set +a
if ! "$PYTHON" -c 'import os,sys;from sqlalchemy.engine import make_url;u=make_url(os.environ["REVIEW_DATABASE_URL"]);sys.exit(0 if u.username=="epidemicreview" and u.password and u.host=="127.0.0.1" else 1)';then
  set -a;source "$PROJECT/.secrets/postgres.env";set +a
  REVIEW_CONFIG="$CONFIG" "$PYTHON" -B "$PROJECT/scripts/source_review/configure_database_access.py" >/dev/null
  unset POSTGRES_ADMIN_PASSWORD POSTGRES_APP_PASSWORD
  set -a;source "$CONFIG";set +a
fi
DEPLOYMENT_COMMIT=$(git -C "$PROJECT" rev-parse HEAD)
export REVIEW_AUTH_FILE="$AUTH_FILE" PYTHONPATH="$RUNTIME/src" APP_ENV=production
export EPIDEMIC_DEPLOYMENT_COMMIT="$DEPLOYMENT_COMMIT" EPIDEMIC_INTERFACE_VERSION=p1-10r3-v1
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY no_proxy NO_PROXY GIT_CONFIG EPIDEMIC_QUERY_API_TOKEN OPENCLAW_GATEWAY_TOKEN VLLM_API_KEY
sh -c "nohup '$PYTHON' -m uvicorn epidemic_core.discovery.review_app:create_app --factory --host '$HOST' --port '$PORT' --workers 1 --no-access-log --no-proxy-headers >'$LOGFILE' 2>&1 & echo \$! >'$PIDFILE'"
for _ in $(seq 1 60);do ready&&exit 0;sleep 1;done;exit 1
