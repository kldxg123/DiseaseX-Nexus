#!/usr/bin/env bash
set -euo pipefail
P1_9_SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
source "$P1_9_SCRIPT_DIR/_common.sh"
source "$PROJECT/scripts/source_discovery_worker/_common.sh"
source "$P1_9_SCRIPT_DIR/_common.sh"
demo_database_gate
export APP_DATABASE_URL="$DEMO_APP_DATABASE_URL"
export P1_9_CANDIDATE_FETCH_TIMEOUT_SECONDS=90
exec "$PYTHON" -m epidemic_core.p1_9.discovery_worker
