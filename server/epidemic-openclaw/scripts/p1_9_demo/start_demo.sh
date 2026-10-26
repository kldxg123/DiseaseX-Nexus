#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
source "$SCRIPT_DIR/_common.sh"
[[ -f "$PG_DATA/PG_VERSION" ]] || { echo 'P1_9_DEMO_CLUSTER_NOT_INITIALIZED' >&2; exit 1; }
if ! "$PG_BIN/pg_ctl" -D "$PG_DATA" status >/dev/null 2>&1; then
  "$PG_BIN/pg_ctl" -D "$PG_DATA" -l "$PG_LOG" -o "-p $PG_PORT -h 127.0.0.1 -k $PG_RUN" start >/dev/null
fi
for _ in $(seq 1 30); do
  "$PG_BIN/pg_isready" -h 127.0.0.1 -p "$PG_PORT" -d epidemic_p1_9_demo >/dev/null 2>&1 && break
  sleep 1
done
demo_database_gate
for name in discovery promotion review dashboard; do
  if alive "$name"; then
    continue
  fi
  case "$name" in
    discovery|promotion) runner="$SCRIPT_DIR/run_${name}_worker.sh" ;;
    review|dashboard) runner="$SCRIPT_DIR/run_${name}.sh" ;;
  esac
  nohup "$runner" >"$LOG_ROOT/$name.log" 2>&1 &
  printf '%s\n' "$!" >"$RUN_ROOT/$name.pid"
  chmod 600 "$RUN_ROOT/$name.pid" "$LOG_ROOT/$name.log"
done
for _ in $(seq 1 60); do
  if alive discovery && alive promotion && alive review && alive dashboard && health "$REVIEW_PORT" && health "$DASHBOARD_PORT"; then
    exit 0
  fi
  sleep 1
done
echo 'P1_9_DEMO_SERVICES_UNHEALTHY' >&2
exit 1
