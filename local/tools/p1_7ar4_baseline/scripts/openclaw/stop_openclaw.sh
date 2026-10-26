#!/usr/bin/env bash
set -euo pipefail
source 'USER_HOME/autodl-tmp/epidemic-openclaw/scripts/openclaw/openclaw_env.sh'
pidfile='USER_HOME/autodl-tmp/epidemic-openclaw/storage/openclaw/run/openclaw.pid';[[ -f "$pidfile" ]]||exit 0;pid=$(<"$pidfile")
if ! kill -0 "$pid" 2>/dev/null;then 'USER_HOME/autodl-tmp/epidemic-openclaw/runtime/node-current/bin/node' -e 'const fs=require("fs");try{fs.unlinkSync(process.argv[1])}catch(e){}' "$pidfile";exit 0;fi
exe=$(readlink -f "/proc/$pid/exe");[[ "$exe" == 'USER_HOME/autodl-tmp/epidemic-openclaw/runtime/node-v24.18.0-linux-x64/bin/node' ]]||exit 2
inode=$(awk '$2=="0100007F:4965"&&$4=="0A"{print $10}' /proc/net/tcp);[[ -n "$inode" ]]||exit 2
found=0;for fd in /proc/$pid/fd/*;do [[ "$(readlink "$fd" 2>/dev/null||true)" == "socket:[$inode]" ]]&&found=1;done;[[ $found == 1 ]]||exit 2
kill -TERM "$pid";for _ in $(seq 1 120);do state=$(awk '{print $3}' "/proc/$pid/stat" 2>/dev/null||true);if [[ -z "$state" || "$state" == Z ]];then 'USER_HOME/autodl-tmp/epidemic-openclaw/runtime/node-current/bin/node' -e 'const fs=require("fs");try{fs.unlinkSync(process.argv[1])}catch(e){}' "$pidfile";exit 0;fi;sleep 1;done;exit 1
