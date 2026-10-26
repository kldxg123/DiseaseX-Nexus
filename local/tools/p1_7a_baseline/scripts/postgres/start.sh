#!/usr/bin/env bash
set -euo pipefail
source USER_HOME/autodl-tmp/epidemic-openclaw/scripts/postgres/_common.sh
if run_pg "$PG_BIN/pg_ctl" -D "$PG_DATA" status >/dev/null 2>&1; then exit 0; fi
run_pg "$PG_BIN/pg_ctl" -D "$PG_DATA" -l "$PG_LOGS/pg_ctl.log" start -w -t 60
