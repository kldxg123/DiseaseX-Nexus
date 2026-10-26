#!/usr/bin/env bash
set -euo pipefail
umask 077
export NODE_HOME='USER_HOME/autodl-tmp/epidemic-openclaw/runtime/node-current' OPENCLAW_HOME='USER_HOME/autodl-tmp/epidemic-openclaw/runtime/openclaw-npm' OPENCLAW_STATE_DIR='USER_HOME/autodl-tmp/epidemic-openclaw/storage/openclaw/state' OPENCLAW_CONFIG_PATH='USER_HOME/autodl-tmp/epidemic-openclaw/storage/openclaw/state/openclaw.json' OPENCLAW_WORKSPACE='USER_HOME/autodl-tmp/epidemic-openclaw/storage/openclaw/workspace'
export PATH="$NODE_HOME/bin:$OPENCLAW_HOME/node_modules/.bin:$PATH"
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
set -a
source "$OPENCLAW_STATE_DIR/.env"
source "USER_HOME/autodl-tmp/epidemic-openclaw/storage/secrets/query-api-token.env"
set +a
