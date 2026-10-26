#!/usr/bin/env bash
set -euo pipefail
source USER_HOME/autodl-tmp/epidemic-openclaw/scripts/retrieval_worker/retrieval_worker_env.sh
run="$PROJECT/storage/run/retrieval-worker";logs="$PROJECT/storage/logs/retrieval-worker";pidfile="$run/worker.pid";lock="$run/start.lock";mkdir -p "$run" "$logs";chmod 700 "$run" "$logs";exec 9>"$lock";flock -x 9
if [[ -f "$pidfile" ]]&&kill -0 "$(<"$pidfile")" 2>/dev/null;then exit 0;fi
log="$logs/worker.log";if [[ -f "$log" ]]&&(( $(stat -c %s "$log") > 5242880 ));then mv "$log" "$log.1";fi
nohup "$PYTHON" -B -m epidemic_core.retrieval.main 9>&- >"$log" 2>&1 & pid=$!;printf '%s\n' "$pid" >"$pidfile"
for _ in $(seq 1 30);do kill -0 "$pid" 2>/dev/null&&sleep 1&&kill -0 "$pid" 2>/dev/null&&exit 0;sleep 1;done
exit 1
