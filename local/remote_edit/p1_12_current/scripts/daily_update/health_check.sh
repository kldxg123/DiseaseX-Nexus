#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)
"$ROOT/scripts/daily_update/status_daily_update.sh"
source "$ROOT/scripts/daily_update/_common.sh"
"$PYTHON" -B -m epidemic_core.daily.cli status >/dev/null
