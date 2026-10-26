#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
source "$SCRIPT_DIR/_common.sh"
[[ -f "$PID_FILE" ]]
pid=$(<"$PID_FILE")
kill -0 "$pid"
cmd=$(tr '\0' ' ' <"/proc/$pid/cmdline")
[[ "$cmd" == *"epidemic_core.daily.main"* ]]
! ss -H -lntp | grep -Fq "pid=$pid,"
"$PYTHON" -B -m epidemic_core.daily.cli status >/dev/null
