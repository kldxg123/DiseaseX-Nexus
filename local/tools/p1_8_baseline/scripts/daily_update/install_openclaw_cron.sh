#!/usr/bin/env bash
set -euo pipefail
set +x
umask 077
PROJECT='USER_HOME/autodl-tmp/epidemic-openclaw'
source "$PROJECT/scripts/openclaw/openclaw_env.sh"
CLI="$PROJECT/scripts/openclaw/openclaw_cli.sh"
PYTHON="$PROJECT/../conda/envs/epidemic-core/bin/python"
NAME='epidemic-daily-update-v1'
CRON='30 2 * * *'
TIMEZONE='Asia/Shanghai'
ARGV='["USER_HOME/autodl-tmp/epidemic-openclaw/scripts/daily_update/reconcile_due.sh","--trigger","openclaw-cron"]'
inventory=$("$CLI" cron list --all --json)
summary=$(printf '%s' "$inventory" | "$PYTHON" -c 'import json,sys;v=json.load(sys.stdin);j=v if isinstance(v,list) else v.get("jobs",v.get("items",[]));m=[x for x in j if x.get("name")=="epidemic-daily-update-v1"];print(len(m));print(m[0].get("id","") if m else "")')
count=$(printf '%s\n' "$summary" | sed -n '1p')
job_id=$(printf '%s\n' "$summary" | sed -n '2p')
if [[ "$count" == '0' ]];then
  "$CLI" cron create --cron "$CRON" --name "$NAME" --description 'Deterministic approved-source daily reconcile' \
    --tz "$TIMEZONE" --exact --command-argv "$ARGV" --command-cwd "$PROJECT" --timeout-seconds 120 \
    --no-output-timeout-seconds 120 --output-max-bytes 4096 --no-deliver --json >/dev/null
elif [[ "$count" == '1' && -n "$job_id" ]];then
  "$CLI" cron edit "$job_id" --name "$NAME" --description 'Deterministic approved-source daily reconcile' \
    --cron "$CRON" --tz "$TIMEZONE" --exact --command-argv "$ARGV" --command-cwd "$PROJECT" \
    --timeout-seconds 120 --no-output-timeout-seconds 120 --output-max-bytes 4096 --no-deliver --enable >/dev/null
else
  echo 'duplicate daily cron jobs require controlled repair' >&2
  exit 1
fi
"$CLI" cron list --all --json | "$PYTHON" -c 'import json,sys;v=json.load(sys.stdin);j=v if isinstance(v,list) else v.get("jobs",v.get("items",[]));m=[x for x in j if x.get("name")=="epidemic-daily-update-v1"];assert len(m)==1 and m[0].get("enabled",True);print(json.dumps({"status":"PASS","job_id":m[0].get("id"),"enabled":m[0].get("enabled",True),"timezone":(m[0].get("schedule") or {}).get("tz","Asia/Shanghai"),"nextRunAt":m[0].get("nextRunAt") or (m[0].get("state") or {}).get("nextRunAt")}))'
