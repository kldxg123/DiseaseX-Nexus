#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)
"$ROOT/scripts/daily_update/stop_daily_update.sh"
"$ROOT/scripts/daily_update/start_daily_update.sh"
