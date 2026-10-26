#!/usr/bin/env bash
set -euo pipefail
PG_BIN='USER_HOME/autodl-tmp/conda/envs/postgresql18/bin'
PG_DATA='USER_HOME/autodl-tmp/epidemic-openclaw/storage/database/postgresql18/data'
PG_RUN='USER_HOME/autodl-tmp/epidemic-openclaw/storage/database/postgresql18/run'
PG_LOGS='USER_HOME/autodl-tmp/epidemic-openclaw/storage/database/postgresql18/logs'
PG_BACKUPS='USER_HOME/autodl-tmp/epidemic-openclaw/storage/database/postgresql18/backups'
SECRETS='USER_HOME/autodl-tmp/epidemic-openclaw/.secrets/postgres.env'
PROJECT='USER_HOME/autodl-tmp/epidemic-openclaw'
run_pg() { "$@"; }
