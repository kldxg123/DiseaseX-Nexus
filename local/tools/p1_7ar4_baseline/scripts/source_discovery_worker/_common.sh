#!/usr/bin/env bash
set -euo pipefail
set +x
umask 077
PROJECT=USER_HOME/autodl-tmp/epidemic-openclaw
PYTHON=USER_HOME/autodl-tmp/conda/envs/epidemic-core/bin/python
RUN="$PROJECT/storage/run/source-discovery-worker"; LOG="$PROJECT/storage/logs/source-discovery-worker"; PIDFILE="$RUN/worker.pid"
mkdir -p "$RUN" "$LOG" "$PROJECT/storage/quarantine/sha256";chmod 700 "$RUN" "$LOG" "$PROJECT/storage/quarantine" "$PROJECT/storage/quarantine/sha256"
set -a;source "$PROJECT/.secrets/postgres.env";set +a
PROVIDER_SECRET="$PROJECT/.secrets/source-discovery-provider.env"
if [[ -e "$PROVIDER_SECRET" ]];then
  [[ -f "$PROVIDER_SECRET" && -r "$PROVIDER_SECRET" && "$(stat -c %a "$PROVIDER_SECRET")" == 600 ]]||exit 1
  set -a;source "$PROVIDER_SECRET";set +a
fi
APP_DATABASE_URL=$("$PYTHON" -c 'import os,urllib.parse;print("postgresql+psycopg://epidemic_app:"+urllib.parse.quote(os.environ["POSTGRES_APP_PASSWORD"],safe="")+"@127.0.0.1:5432/epidemic_openclaw")')
export APP_DATABASE_URL DISCOVERY_QUARANTINE_ROOT="$PROJECT/storage/quarantine/sha256" PYTHONPATH="$PROJECT/src"
export DISCOVERY_DIRECTORY_REGISTRY="$PROJECT/config/source_discovery/directories.v1.json"
unset POSTGRES_ADMIN_PASSWORD POSTGRES_APP_PASSWORD EPIDEMIC_QUERY_API_TOKEN OPENCLAW_GATEWAY_TOKEN VLLM_API_KEY
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY no_proxy NO_PROXY GIT_CONFIG
alive(){ [[ -f "$PIDFILE" ]]&&kill -0 "$(<"$PIDFILE")" 2>/dev/null&&grep -aq 'epidemic_core.discovery.worker' "/proc/$(<"$PIDFILE")/cmdline"; }
healthy(){
  alive||return 1
  local pid;pid=$(<"$PIDFILE")
  WORKER_PID="$pid" "$PYTHON" -c 'import os,sqlalchemy as s; e=s.create_engine(os.environ["APP_DATABASE_URL"],pool_pre_ping=True); print(e.connect().execute(s.text("SELECT epidemic_discovery.is_discovery_worker_healthy(:pid)"),{"pid":int(os.environ["WORKER_PID"])}).scalar_one())' 2>/dev/null | grep -qx True
}
provider_model(){
  "$PYTHON" -c 'from epidemic_core.discovery.providers import DirectoryRegistry,capability_snapshot; d=capability_snapshot(DirectoryRegistry.load()); ids={x["provider_id"] for x in d["providers"]}; assert {"brave-web-search-v1","datacite-public-api-v2","ckan-adapter-v1","socrata-adapter-v1","odata-adapter-v1"}<=ids' >/dev/null
}
