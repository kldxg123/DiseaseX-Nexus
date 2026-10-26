#!/usr/bin/env bash
set -euo pipefail
set +x
umask 077
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
PROJECT=$(cd -- "$SCRIPT_DIR/../.." && pwd -P)
ROOT=$(cd -- "$PROJECT/.." && pwd -P)
PYTHON="$ROOT/conda/envs/epidemic-core/bin/python"
PG_BIN="$ROOT/conda/envs/postgresql18/bin"
DEMO_ROOT="$PROJECT/storage/p1_9_demo"
PG_DATA="$DEMO_ROOT/postgresql18/data"
PG_RUN="$DEMO_ROOT/postgresql18/run"
PG_LOG="$DEMO_ROOT/log/postgresql.log"
QUARANTINE_ROOT="$DEMO_ROOT/quarantine/sha256"
RAW_ROOT="$DEMO_ROOT/raw/sha256"
RUN_ROOT="$DEMO_ROOT/run"
LOG_ROOT="$DEMO_ROOT/log"
AUTH_FILE="$DEMO_ROOT/secrets/login_hash.json"
SECRETS="$PROJECT/.secrets/p1_9_demo.env"
PG_PORT=15432
REVIEW_HOST=127.0.0.1
REVIEW_PORT=18021
DASHBOARD_HOST=127.0.0.1
DASHBOARD_PORT=18020
mkdir -p "$PG_RUN" "$RUN_ROOT" "$LOG_ROOT" "$QUARANTINE_ROOT" "$RAW_ROOT"
chmod 700 "$DEMO_ROOT" "$PG_RUN" "$RUN_ROOT" "$LOG_ROOT" "$QUARANTINE_ROOT" "$RAW_ROOT"
[[ -r "$SECRETS" && -r "$AUTH_FILE" ]] || { echo 'P1_9_DEMO_CONFIGURATION_MISSING' >&2; exit 1; }
set -a
source "$SECRETS"
set +a
export APP_ENV=p1_9_demo PROMOTION_RAW_ROOT="$RAW_ROOT" DISCOVERY_QUARANTINE_ROOT="$QUARANTINE_ROOT"
export PYTHONPATH="$PROJECT/src"
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY no_proxy NO_PROXY GIT_CONFIG

demo_database_gate() {
  "$PYTHON" -B - <<'PY'
import os
import sqlalchemy as sa
from sqlalchemy import text
engine=sa.create_engine(os.environ["DEMO_APP_DATABASE_URL"],hide_parameters=True)
with engine.connect() as db:
    name=db.execute(text("SELECT current_database()" )).scalar_one()
    marker=db.execute(text("SELECT disposable AND production_write_forbidden AND created_for='P1-9' AND database_name=current_database() FROM epidemic_discovery.p1_9_environment_markers WHERE environment_type='p1_9_demo'" )).scalar_one_or_none()
if "p1_9_demo" not in name or marker is not True:
    raise SystemExit("P1_9_DEMO_DATABASE_GATE_FAILED")
PY
}

alive() {
  local name=$1 pidfile="$RUN_ROOT/$1.pid"
  [[ -f "$pidfile" ]] && kill -0 "$(<"$pidfile")" 2>/dev/null
}

health() {
  local port=$1
  curl --silent --fail --noproxy '*' --max-time 5 "http://127.0.0.1:$port/healthz" >/dev/null
}
