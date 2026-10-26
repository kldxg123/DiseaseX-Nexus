#!/usr/bin/env bash
set -euo pipefail
sql_file="$1"
cd USER_HOME/projects/openclaw
runtime/postgres18x/bin/psql \
  -h USER_HOME/projects/openclaw/runtime/postgres-run \
  -p 55432 \
  -U postgres \
  -d epidemic_openclaw \
  -X \
  -v ON_ERROR_STOP=1 \
  -f "$sql_file"
