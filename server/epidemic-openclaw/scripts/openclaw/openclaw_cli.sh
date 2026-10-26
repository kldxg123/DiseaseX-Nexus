#!/usr/bin/env bash
set -euo pipefail
source 'USER_HOME/autodl-tmp/epidemic-openclaw/scripts/openclaw/openclaw_env.sh'
exec 'USER_HOME/autodl-tmp/epidemic-openclaw/runtime/openclaw-npm/node_modules/.bin/openclaw' "$@"
