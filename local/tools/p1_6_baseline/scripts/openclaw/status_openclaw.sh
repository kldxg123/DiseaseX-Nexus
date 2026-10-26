#!/usr/bin/env bash
set -euo pipefail
source 'USER_HOME/autodl-tmp/epidemic-openclaw/scripts/openclaw/openclaw_env.sh'
pidfile='USER_HOME/autodl-tmp/epidemic-openclaw/storage/openclaw/run/openclaw.pid';[[ -f "$pidfile" ]];pid=$(<"$pidfile");kill -0 "$pid"
grep -qx '0100007F:4965' <(awk 'NR>1&&$4=="0A"{print $2}' /proc/net/tcp)
! grep -q ':4965$' <(awk 'NR>1&&$4=="0A"{print $2}' /proc/net/tcp6)
'USER_HOME/autodl-tmp/epidemic-openclaw/scripts/openclaw/openclaw_cli.sh' gateway call health --json >/dev/null
