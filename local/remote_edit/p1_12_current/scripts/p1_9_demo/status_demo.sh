#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
source "$SCRIPT_DIR/_common.sh"
demo_database_gate
for name in discovery promotion review dashboard; do alive "$name" || exit 1; done
health "$REVIEW_PORT"
health "$DASHBOARD_PORT"
"$PG_BIN/pg_isready" -h 127.0.0.1 -p "$PG_PORT" -d epidemic_p1_9_demo >/dev/null
