#!/usr/bin/env bash
set -euo pipefail
ROOT=USER_HOME/projects/openclaw
PID_FILE="$ROOT/runtime/dashboard.pid"
LOG_FILE="$ROOT/runtime/dashboard.log"
if [[ -f "$PID_FILE" ]]; then
  pid="$(cat "$PID_FILE")"
  if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
    kill "$pid"
    for _ in {1..30}; do
      if kill -0 "$pid" 2>/dev/null; then
        sleep 1
      else
        break
      fi
    done
  fi
fi
nohup "$ROOT/runtime/start_vm201_dashboard.sh" >>"$LOG_FILE" 2>&1 &
new_pid="$!"
echo "$new_pid" >"$PID_FILE"
sleep 2
if ! kill -0 "$new_pid" 2>/dev/null; then
  echo "DASHBOARD_RESTART_FAILED"
  tail -n 80 "$LOG_FILE"
  exit 1
fi
echo "DASHBOARD_RESTARTED"
