#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
source "$SCRIPT_DIR/_common.sh"
[[ -f "$PID_FILE" ]] || exit 0
pid=$(<"$PID_FILE")
if kill -0 "$pid" 2>/dev/null;then
  cmd=$(tr '\0' ' ' <"/proc/$pid/cmdline")
  [[ "$cmd" == *"epidemic_core.daily.main"* ]] || exit 1
  kill "$pid"
  for _ in $(seq 1 30);do kill -0 "$pid" 2>/dev/null || break;sleep 1;done
  kill -0 "$pid" 2>/dev/null && exit 1
fi
rm -f "$PID_FILE"
