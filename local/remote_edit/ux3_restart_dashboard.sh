#!/usr/bin/env bash
set -euo pipefail
cd USER_HOME/projects/openclaw
old_pid=""
while read -r pid cmd; do
  case "$cmd" in
    *"uvicorn epidemic_core.dashboard.app"*) old_pid="$pid"; break ;;
  esac
done < <(ps -eo pid=,args=)
if [ -n "$old_pid" ]; then
  kill "$old_pid" || true
fi
sleep 1
nohup bash runtime/start_vm201_dashboard.sh > runtime/logs/dashboard_8020.log 2>&1 &
new_pid=$!
echo "$new_pid" > runtime/dashboard.pid
sleep 3
python3 - <<'PY'
import urllib.request
for p in ['/healthz','/readyz']:
    with urllib.request.urlopen('http://127.0.0.1:8020'+p, timeout=8) as r:
        print(p, r.status, r.read(160).decode('utf-8','ignore'))
PY
ps -eo pid=,ppid=,args= | grep 'uvicorn epidemic_core.dashboard.app' | grep -v grep
