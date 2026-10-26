#!/usr/bin/env bash
set -euo pipefail
source 'USER_HOME/autodl-tmp/epidemic-openclaw/scripts/qwen_vllm/_common.sh'
[[ -f "$PIDFILE" ]] || exit 0
pid=$(<"$PIDFILE"); if ! kill -0 "$pid" 2>/dev/null; then USER_HOME/miniconda3/bin/python -c 'import os,sys; os.unlink(sys.argv[1]) if os.path.exists(sys.argv[1]) else None' "$PIDFILE"; exit 0; fi
kill -TERM "$pid"
for _ in $(seq 1 180); do if ! kill -0 "$pid" 2>/dev/null; then USER_HOME/miniconda3/bin/python -c 'import os,sys; os.unlink(sys.argv[1]) if os.path.exists(sys.argv[1]) else None' "$PIDFILE"; exit 0; fi; sleep 1; done
exit 1
