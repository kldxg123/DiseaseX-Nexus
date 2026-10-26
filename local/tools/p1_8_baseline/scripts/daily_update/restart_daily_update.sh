#!/usr/bin/env bash
set -euo pipefail
ROOT='USER_HOME/autodl-tmp/epidemic-openclaw'
"$ROOT/scripts/daily_update/stop_daily_update.sh"
"$ROOT/scripts/daily_update/start_daily_update.sh"
