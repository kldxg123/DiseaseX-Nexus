#!/usr/bin/env bash
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
source "$SCRIPT_DIR/_common.sh"
[[ -f "$PIDFILE" ]]||exit 0;pid=$(<"$PIDFILE");kill "$pid" 2>/dev/null||true
for _ in $(seq 1 30);do kill -0 "$pid" 2>/dev/null||{ rm -f "$PIDFILE";exit 0;};sleep 1;done;exit 1
