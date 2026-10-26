#!/usr/bin/env bash
set -euo pipefail
set +x
source USER_HOME/autodl-tmp/epidemic-openclaw/scripts/query_api/query_api_env.sh
pidfile="$PROJECT/storage/run/query-api/query-api.pid";[[ -f "$pidfile" ]]||exit 0;pid=$(<"$pidfile")
if ! kill -0 "$pid" 2>/dev/null;then rm -f "$pidfile";exit 0;fi
exe=$(readlink -f "/proc/$pid/exe");[[ "$exe" == 'USER_HOME/autodl-tmp/conda/envs/epidemic-core/bin/python3.10' || "$exe" == 'USER_HOME/autodl-tmp/conda/envs/epidemic-core/bin/python' ]]
cmd=$(tr '\0' ' ' <"/proc/$pid/cmdline");[[ "$cmd" == *"uvicorn"* && "$cmd" == *"epidemic_core.api.app:create_app"* ]]
kill -TERM "$pid";for _ in $(seq 1 60);do state=$(awk '{print $3}' "/proc/$pid/stat" 2>/dev/null||true);[[ -z "$state" || "$state" == Z ]]&&rm -f "$pidfile"&&exit 0;sleep 1;done;exit 1
