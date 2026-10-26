#!/usr/bin/env bash
set -euo pipefail
source 'USER_HOME/autodl-tmp/epidemic-openclaw/scripts/daily_update/_common.sh'
[[ $# -eq 2 && "$1" == '--trigger' ]]
case "$2" in openclaw-cron|startup) trigger="$2";; *) exit 64;; esac
exec "$PYTHON" -B -m epidemic_core.daily.cli reconcile-due --trigger "$trigger"
