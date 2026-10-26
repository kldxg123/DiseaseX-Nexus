#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
source "$SCRIPT_DIR/_common.sh"
demo_database_gate
export APP_DATABASE_URL="$DEMO_APP_DATABASE_URL"
exec "$PYTHON" -m epidemic_core.p1_9.promotion
