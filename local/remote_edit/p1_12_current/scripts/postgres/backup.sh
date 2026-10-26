#!/usr/bin/env bash
set -euo pipefail
source USER_HOME/autodl-tmp/epidemic-openclaw/scripts/postgres/_common.sh
ts=$(date -u +%Y%m%dT%H%M%SZ); file="$PG_BACKUPS/epidemic_openclaw-$ts.dump"
run_pg "$PG_BIN/pg_dump" -h "$PG_RUN" -U epidemicdb -d epidemic_openclaw -Fc -f "$file"
sha256sum "$file" >"$file.sha256"
chmod 640 "$file" "$file.sha256"
printf '%s\n' "$file"
