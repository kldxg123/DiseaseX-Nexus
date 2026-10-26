#!/usr/bin/env bash
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
source "$SCRIPT_DIR/_common.sh"
if alive;then healthy&&provider_model&&exit 0;exit 1;fi
rm -f "$PIDFILE"
"$SCRIPT_DIR/install_extractor_runtime.sh"
nohup "$PYTHON" -m epidemic_core.discovery.worker >"$LOG/worker.log" 2>&1 & pid=$!;printf '%s\n' "$pid" >"$PIDFILE"
for _ in $(seq 1 30);do healthy&&provider_model&&exit 0;sleep 1;done
exit 1
