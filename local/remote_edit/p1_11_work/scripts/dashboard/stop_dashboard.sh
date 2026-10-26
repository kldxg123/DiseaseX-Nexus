#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/_common.sh"
if dashboard_alive; then
  pid=$(<"$PIDFILE")
  kill "$pid" 2>/dev/null || true
  for _ in $(seq 1 30); do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
  kill -9 "$pid" 2>/dev/null || true
fi
rm -f "$PIDFILE"
