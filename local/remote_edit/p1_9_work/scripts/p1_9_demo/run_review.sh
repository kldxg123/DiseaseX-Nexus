#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
source "$SCRIPT_DIR/_common.sh"
demo_database_gate
export REVIEW_DATABASE_URL="$DEMO_REVIEW_DATABASE_URL" REVIEW_AUTH_FILE="$AUTH_FILE"
export REVIEW_LOGIN_REDIRECT=/p1-9/reviews REVIEW_PUBLIC_ORIGIN=http://127.0.0.1:8123
export P1_9_PUBLIC_ORIGIN=http://127.0.0.1:8123
exec "$PYTHON" -m uvicorn epidemic_core.discovery.review_app:create_app --factory \
  --host "$REVIEW_HOST" --port "$REVIEW_PORT" --workers 1 --no-access-log --no-proxy-headers
