#!/usr/bin/env bash
set -euo pipefail
source USER_HOME/autodl-tmp/epidemic-openclaw/scripts/postgres/_common.sh
run_pg "$PG_BIN/pg_ctl" -D "$PG_DATA" status
"$PG_BIN/pg_isready" -h 127.0.0.1 -p 5432 -d epidemic_openclaw
