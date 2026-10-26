#!/usr/bin/env bash
set -euo pipefail
pid="$(cat USER_HOME/projects/openclaw/runtime/dashboard.pid 2>/dev/null || pgrep -f 'uvicorn.*epidemic_core.dashboard' | head -n1)"
echo "exe=$(readlink -f "/proc/$pid/exe")"
tr '\0' '\n' < "/proc/$pid/environ" | awk -F= '/^(PATH|VIRTUAL_ENV|CONDA_PREFIX|PYTHONPATH)=/{print $1"=<set>"}'
