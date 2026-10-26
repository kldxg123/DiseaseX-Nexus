#!/usr/bin/env bash
set -euo pipefail
cd USER_HOME/projects/openclaw
runtime/postgres18x/bin/psql \
  -h USER_HOME/projects/openclaw/runtime/postgres-run \
  -p 55432 \
  -U postgres \
  -d epidemic_openclaw \
  -X \
  -tAc "select current_user, current_database(), current_schema(), version();"
