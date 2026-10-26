#!/usr/bin/env bash
set -euo pipefail
source 'USER_HOME/autodl-tmp/epidemic-openclaw/scripts/daily_update/_common.sh'
exec 9>"$RUN_DIR/start.lock"
flock -x 9
if [[ -f "$PID_FILE" ]];then
  pid=$(<"$PID_FILE")
  if kill -0 "$pid" 2>/dev/null;then
    cmd=$(tr '\0' ' ' <"/proc/$pid/cmdline")
    [[ "$cmd" == *"epidemic_core.daily.main"* ]] && exit 0
    exit 1
  fi
  rm -f "$PID_FILE"
fi
find "$LOG_DIR" -maxdepth 1 -type f -name 'worker-*.log' -mtime +14 -delete
log="$LOG_DIR/worker-$(date -u +%Y%m%dT%H%M%SZ).log"
nohup "$PYTHON" -B -m epidemic_core.daily.main 9>&- >"$log" 2>&1 &
pid=$!
printf '%s\n' "$pid" >"$PID_FILE"
for _ in $(seq 1 30);do
  if ! kill -0 "$pid" 2>/dev/null;then tail -n 40 "$log";exit 1;fi
  cmd=$(tr '\0' ' ' <"/proc/$pid/cmdline")
  [[ "$cmd" == *"epidemic_core.daily.main"* ]] && exit 0
  sleep 1
done
exit 1
