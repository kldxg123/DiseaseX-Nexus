#!/usr/bin/env bash
source USER_HOME/autodl-tmp/epidemic-openclaw/scripts/source_review/_common.sh
if alive;then health&&exit 0;exit 1;fi
install -d -o epidemicreview -g epidemicreview -m 0700 "$RUNTIME/run" "$RUNTIME/log"
set -a;source "$CONFIG";set +a
export REVIEW_AUTH_FILE="$AUTH_FILE" PYTHONPATH="$RUNTIME/src"
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY no_proxy NO_PROXY GIT_CONFIG EPIDEMIC_QUERY_API_TOKEN OPENCLAW_GATEWAY_TOKEN VLLM_API_KEY
runuser -u epidemicreview -- sh -c "nohup '$PYTHON' -m uvicorn epidemic_core.discovery.review_app:create_app --factory --host '$HOST' --port '$PORT' --workers 1 --no-access-log --no-proxy-headers >'$LOGFILE' 2>&1 & echo \$! >'$PIDFILE'"
for _ in $(seq 1 60);do health&&exit 0;sleep 1;done;exit 1
