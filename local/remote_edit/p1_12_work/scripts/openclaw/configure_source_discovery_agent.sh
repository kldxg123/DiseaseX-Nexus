#!/usr/bin/env bash
set -euo pipefail
set +x
umask 077
PROJECT=USER_HOME/autodl-tmp/epidemic-openclaw
PYTHON=USER_HOME/autodl-tmp/conda/envs/epidemic-core/bin/python
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
"$PYTHON" "$PROJECT/scripts/openclaw/configure_source_discovery_agent.py"
source "$PROJECT/scripts/openclaw/openclaw_env.sh"
openclaw config validate >/dev/null

