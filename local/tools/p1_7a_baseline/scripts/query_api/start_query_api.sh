#!/usr/bin/env bash
set -euo pipefail
set +x
source USER_HOME/autodl-tmp/epidemic-openclaw/scripts/query_api/query_api_env.sh
run="$PROJECT/storage/run/query-api";logs="$PROJECT/storage/logs/query-api";pidfile="$run/query-api.pid";log="$logs/query-api.log"
mkdir -p "$run" "$logs";chmod 700 "$run" "$logs"
if [[ -f "$pidfile" ]]&&kill -0 "$(<"$pidfile")" 2>/dev/null;then "$PYTHON" "$PROJECT/scripts/query_api/probe.py" /healthz >/dev/null&&exit 0;exit 1;fi
if awk 'NR>1&&$2~/:1F4A$/&&$4=="0A"{found=1}END{exit !found}' /proc/net/tcp /proc/net/tcp6;then exit 2;fi
if [[ -f "$log" ]]&&(( $(stat -c %s "$log") > 5242880 ));then mv "$log" "$log.1";fi
nohup "$PYTHON" -m uvicorn --factory epidemic_core.api.app:create_app --host 127.0.0.1 --port 8010 --workers 1 --no-access-log --no-proxy-headers --limit-concurrency 64 --timeout-keep-alive 5 >"$log" 2>&1 &
pid=$!;printf '%s\n' "$pid" >"$pidfile"
for _ in $(seq 1 120);do
  if ! kill -0 "$pid" 2>/dev/null;then tail -n 40 "$log"|sed -E 's#(token|password|secret|authorization)[^ ]*#<redacted>#Ig';exit 1;fi
  if "$PYTHON" "$PROJECT/scripts/query_api/probe.py" /readyz >/dev/null 2>&1;then exit 0;fi
  sleep 1
done
exit 1
