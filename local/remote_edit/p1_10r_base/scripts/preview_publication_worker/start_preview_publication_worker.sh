#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
source "$SCRIPT_DIR/_common.sh"
if alive; then
  exit 0
fi
rm -f "$PIDFILE"
load_preview_environment
nohup "$PYTHON" -m epidemic_core.p1_10.preview >"$LOG/worker.log" 2>&1 &
printf '%s\n' "$!" >"$PIDFILE"
chmod 0600 "$PIDFILE" "$LOG/worker.log"
for _ in $(seq 1 30); do
  alive && exit 0
  sleep 1
done
exit 1
