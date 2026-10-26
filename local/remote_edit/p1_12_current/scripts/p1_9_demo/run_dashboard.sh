#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
source "$SCRIPT_DIR/_common.sh"
demo_database_gate
export DASHBOARD_DATABASE_URL="$DEMO_DASHBOARD_DATABASE_URL" DASHBOARD_AUTH_FILE="$AUTH_FILE"
exec "$PYTHON" -m uvicorn epidemic_core.p1_9.dashboard_app:create_app --factory \
  --host "$DASHBOARD_HOST" --port "$DASHBOARD_PORT" --workers 1 --no-access-log --no-proxy-headers
