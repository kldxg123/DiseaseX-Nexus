#!/usr/bin/env bash
set -euo pipefail
source USER_HOME/autodl-tmp/epidemic-openclaw/scripts/retrieval_worker/retrieval_worker_env.sh
pidfile="$PROJECT/storage/run/retrieval-worker/worker.pid";[[ -f "$pidfile" ]]||exit 0;pid=$(<"$pidfile")
if ! kill -0 "$pid" 2>/dev/null;then "$PYTHON" -c 'import os,sys;os.unlink(sys.argv[1]) if os.path.exists(sys.argv[1]) else None' "$pidfile";exit 0;fi
cmd=$(tr '\0' ' ' <"/proc/$pid/cmdline");[[ "$cmd" == *"epidemic_core.retrieval.main"* ]];kill -TERM "$pid"
for _ in $(seq 1 60);do state=$(awk '{print $3}' "/proc/$pid/stat" 2>/dev/null||true);if [[ -z "$state" || "$state" == Z ]];then [[ -f "$pidfile" ]]&&[[ "$(<"$pidfile")" == "$pid" ]]&&"$PYTHON" -c 'import os,sys;os.unlink(sys.argv[1]) if os.path.exists(sys.argv[1]) else None' "$pidfile";exit 0;fi;sleep 1;done
exit 1
