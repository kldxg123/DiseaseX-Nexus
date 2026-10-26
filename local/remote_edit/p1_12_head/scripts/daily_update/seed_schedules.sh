#!/usr/bin/env bash
set -euo pipefail
PROJECT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)
PYTHON="$PROJECT/../conda/envs/epidemic-core/bin/python"
[[ -x "$PYTHON" ]]
DATABASE_URL='postgresql+psycopg://REDACTED@/epidemic_openclaw?host=%2Froot%2Fautodl-tmp%2Fepidemic-openclaw%2Fstorage%2Fdatabase%2Fpostgresql18%2Frun'
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
exec env PYTHONPATH="$PROJECT/src" APP_DATABASE_URL="$DATABASE_URL" \
  "$PYTHON" -B -m epidemic_core.daily.cli seed-schedules
