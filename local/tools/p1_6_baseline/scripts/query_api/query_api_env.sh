#!/usr/bin/env bash
set -euo pipefail
set +x
umask 077
PROJECT='USER_HOME/autodl-tmp/epidemic-openclaw'
PYTHON="$PROJECT/../conda/envs/epidemic-core/bin/python"
[[ -x "$PYTHON" ]]
set -a
source "$PROJECT/.secrets/postgres.env"
source "$PROJECT/storage/secrets/query-api-token.env"
set +a
APP_DATABASE_URL=$("$PYTHON" -c 'import os,urllib.parse;print("postgresql"+"+psycopg://epidemic_app:"+urllib.parse.quote(os.environ["POSTGRES_APP_PASSWORD"],safe="")+"@127.0.0.1:5432/epidemic_openclaw")')
export APP_DATABASE_URL
unset POSTGRES_ADMIN_PASSWORD POSTGRES_APP_PASSWORD
if [[ -f "$PROJECT/storage/secrets/query-api-test-database.env" ]];then
  set -a
  source "$PROJECT/storage/secrets/query-api-test-database.env"
  set +a
fi
[[ ${#EPIDEMIC_QUERY_API_TOKEN} -ge 32 ]]
export PYTHONPATH="$PROJECT/src"
export PYTHON
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
