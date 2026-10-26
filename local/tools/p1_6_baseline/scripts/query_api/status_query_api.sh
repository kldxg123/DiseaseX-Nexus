#!/usr/bin/env bash
set -euo pipefail
set +x
source USER_HOME/autodl-tmp/epidemic-openclaw/scripts/query_api/query_api_env.sh
pidfile="$PROJECT/storage/run/query-api/query-api.pid";[[ -f "$pidfile" ]];pid=$(<"$pidfile");kill -0 "$pid"
[[ $(awk 'NR>1&&$2=="0100007F:1F4A"&&$4=="0A"{n++}END{print n+0}' /proc/net/tcp) == 1 ]]
[[ $(awk 'NR>1&&$2~/:1F4A$/&&$2!="0100007F:1F4A"&&$4=="0A"{n++}END{print n+0}' /proc/net/tcp /proc/net/tcp6) == 0 ]]
"$PYTHON" "$PROJECT/scripts/query_api/probe.py" /healthz >/dev/null
"$PYTHON" "$PROJECT/scripts/query_api/probe.py" /readyz >/dev/null
