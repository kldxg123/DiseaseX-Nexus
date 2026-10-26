#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
source "$SCRIPT_DIR/_common.sh"
if alive; then
  kill "$(<"$PIDFILE")"
  for _ in $(seq 1 30); do
    alive || break
    sleep 1
  done
fi
rm -f "$PIDFILE"
