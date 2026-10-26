#!/usr/bin/env bash
source USER_HOME/autodl-tmp/epidemic-openclaw/scripts/source_discovery_worker/_common.sh
if [[ -f "$PIDFILE" ]]&&kill -0 "$(<"$PIDFILE")" 2>/dev/null;then exit 0;fi
nohup "$PYTHON" -m epidemic_core.discovery.worker >"$LOG/worker.log" 2>&1 & echo $! >"$PIDFILE"
for _ in $(seq 1 30);do kill -0 "$(<"$PIDFILE")" 2>/dev/null&&exit 0;sleep 1;done
exit 1
