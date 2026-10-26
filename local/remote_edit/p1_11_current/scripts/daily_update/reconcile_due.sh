#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
source "$SCRIPT_DIR/_common.sh"
[[ $# -eq 2 && "$1" == '--trigger' ]]
case "$2" in openclaw-cron|startup) trigger="$2";; *) exit 64;; esac
exec "$PYTHON" -B -m epidemic_core.daily.cli reconcile-due --trigger "$trigger"
