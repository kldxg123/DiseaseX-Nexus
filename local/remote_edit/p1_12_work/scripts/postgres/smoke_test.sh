#!/usr/bin/env bash
set -euo pipefail
source USER_HOME/autodl-tmp/epidemic-openclaw/scripts/postgres/_common.sh
source "$SECRETS"
pgpass=$(mktemp "$PG_RUN/.pgpass.XXXXXX")
trap 'USER_HOME/autodl-tmp/conda/envs/epidemic-core/bin/python -c '"'"'import os,sys; os.unlink(sys.argv[1]) if os.path.exists(sys.argv[1]) else None'"'"' "$pgpass"' EXIT
printf '%s:%s:%s:%s:%s\n' "$POSTGRES_HOST" "$POSTGRES_PORT" "$POSTGRES_DB" "$POSTGRES_APP_USER" "$POSTGRES_APP_PASSWORD" >"$pgpass"
chmod 600 "$pgpass"
PGPASSFILE="$pgpass" "$PG_BIN/psql" -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_APP_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -Atc "SELECT key FROM epidemic.platform_state WHERE key='p0_2_sentinel'" >/dev/null
