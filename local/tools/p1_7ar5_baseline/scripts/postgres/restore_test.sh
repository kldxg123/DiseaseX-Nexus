#!/usr/bin/env bash
set -euo pipefail
source USER_HOME/autodl-tmp/epidemic-openclaw/scripts/postgres/_common.sh
file=${1:?backup path required}
run_pg "$PG_BIN/dropdb" -h "$PG_RUN" -U epidemicdb --if-exists epidemic_restore_test
run_pg "$PG_BIN/createdb" -h "$PG_RUN" -U epidemicdb -O epidemic_app epidemic_restore_test
trap 'run_pg "$PG_BIN/dropdb" -h "$PG_RUN" -U epidemicdb --if-exists epidemic_restore_test >/dev/null' EXIT
run_pg "$PG_BIN/pg_restore" -h "$PG_RUN" -U epidemicdb -d epidemic_restore_test --no-owner "$file"
a=$(run_pg "$PG_BIN/psql" -h "$PG_RUN" -U epidemicdb -d epidemic_openclaw -Atc "SELECT value::text FROM epidemic.platform_state WHERE key='p0_2_sentinel'")
b=$(run_pg "$PG_BIN/psql" -h "$PG_RUN" -U epidemicdb -d epidemic_restore_test -Atc "SELECT value::text FROM epidemic.platform_state WHERE key='p0_2_sentinel'")
test "$a" = "$b"
