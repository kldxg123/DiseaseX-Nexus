#!/usr/bin/env bash
set -euo pipefail
set +x
umask 077
PROJECT='USER_HOME/autodl-tmp/epidemic-openclaw'
PYTHON="$PROJECT/../conda/envs/epidemic-core/bin/python"
[[ -x "$PYTHON" ]]
set -a
source "$PROJECT/.secrets/postgres.env"
set +a
APP_DATABASE_URL=$("$PYTHON" -c 'import os,urllib.parse;print("postgresql"+"+psycopg://epidemic_app:"+urllib.parse.quote(os.environ["POSTGRES_APP_PASSWORD"],safe="")+"@127.0.0.1:5432/epidemic_openclaw")')
export APP_DATABASE_URL RAW_ARTIFACT_ROOT="$PROJECT/storage/raw/sha256" PYTHONPATH="$PROJECT/src" PYTHON
if [[ -f "$PROJECT/storage/secrets/retrieval-worker-test.env" ]];then
  set -a
  source "$PROJECT/storage/secrets/retrieval-worker-test.env"
  set +a
fi
unset POSTGRES_ADMIN_PASSWORD POSTGRES_APP_PASSWORD EPIDEMIC_QUERY_API_TOKEN OPENCLAW_GATEWAY_TOKEN VLLM_API_KEY
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY no_proxy NO_PROXY GIT_CONFIG
