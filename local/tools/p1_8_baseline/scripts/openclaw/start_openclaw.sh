#!/usr/bin/env bash
set -euo pipefail
source 'USER_HOME/autodl-tmp/epidemic-openclaw/scripts/openclaw/openclaw_env.sh'
pidfile='USER_HOME/autodl-tmp/epidemic-openclaw/storage/openclaw/run/openclaw.pid'; logfile='USER_HOME/autodl-tmp/epidemic-openclaw/storage/openclaw/logs/gateway.log'
if [[ -f "$pidfile" ]] && kill -0 "$(<"$pidfile")" 2>/dev/null; then 'USER_HOME/autodl-tmp/epidemic-openclaw/scripts/openclaw/openclaw_cli.sh' gateway call health --json >/dev/null 2>&1 && exit 0; exit 1; fi
'USER_HOME/autodl-tmp/epidemic-openclaw/runtime/node-current/bin/node' -e 'const fs=require("fs");try{fs.unlinkSync(process.argv[1])}catch(e){if(e.code!=="ENOENT")throw e}' "$pidfile"
started=$(date +%s); nohup 'USER_HOME/autodl-tmp/epidemic-openclaw/runtime/openclaw-npm/node_modules/.bin/openclaw' gateway run --port 18789 --bind loopback >"$logfile" 2>&1 & pid=$!
printf '%s\n' "$pid" >"$pidfile"; printf '%s\n' "$started" >'USER_HOME/autodl-tmp/epidemic-openclaw/storage/openclaw/run/start_epoch'
for _ in $(seq 1 180);do if ! kill -0 "$pid" 2>/dev/null;then tail -n 60 "$logfile"|sed -E 's/(token|password|key)[^ ]*/<redacted>/Ig';exit 1;fi;if 'USER_HOME/autodl-tmp/epidemic-openclaw/scripts/openclaw/openclaw_cli.sh' gateway call health --json >/dev/null 2>&1;then date +%s >'USER_HOME/autodl-tmp/epidemic-openclaw/storage/openclaw/run/ready_epoch';exit 0;fi;sleep 1;done
tail -n 60 "$logfile"|sed -E 's/(token|password|key)[^ ]*/<redacted>/Ig';exit 1
