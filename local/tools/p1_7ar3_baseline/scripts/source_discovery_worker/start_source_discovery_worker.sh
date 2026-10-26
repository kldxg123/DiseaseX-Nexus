#!/usr/bin/env bash
source USER_HOME/autodl-tmp/epidemic-openclaw/scripts/source_discovery_worker/_common.sh
if alive;then healthy&&exit 0;exit 1;fi
rm -f "$PIDFILE"
nohup "$PYTHON" -m epidemic_core.discovery.worker >"$LOG/worker.log" 2>&1 & pid=$!;printf '%s\n' "$pid" >"$PIDFILE"
for _ in $(seq 1 30);do healthy&&exit 0;sleep 1;done
exit 1
