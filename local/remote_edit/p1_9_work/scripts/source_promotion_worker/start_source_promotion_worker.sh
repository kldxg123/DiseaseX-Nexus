#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
source "$SCRIPT_DIR/_common.sh"
if [[ -f "$PIDFILE" ]]&&kill -0 "$(<"$PIDFILE")" 2>/dev/null;then exit 0;fi
nohup "$PYTHON" -m epidemic_core.discovery.promotion >"$LOG/worker.log" 2>&1 & echo $! >"$PIDFILE"
for _ in $(seq 1 30);do kill -0 "$(<"$PIDFILE")" 2>/dev/null&&exit 0;sleep 1;done;exit 1
