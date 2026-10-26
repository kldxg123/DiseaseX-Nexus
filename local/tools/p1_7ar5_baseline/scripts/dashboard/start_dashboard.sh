#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/_common.sh"
if dashboard_alive && dashboard_health; then exit 0; fi
if dashboard_alive; then kill "$(<"$PIDFILE")" 2>/dev/null || true; fi
rm -f "$PIDFILE"
[[ -r "$CONFIG" && -r "$AUTH_FILE" ]] || { echo 'dashboard configuration unavailable' >&2; exit 1; }
for key in http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY GIT_CONFIG; do unset "$key" || true; done
runuser -u epidemicui -- bash -c "set -a; source '$CONFIG'; set +a; export DASHBOARD_AUTH_FILE='$AUTH_FILE' PYTHONPATH='$RUNTIME/src'; exec '$PYTHON' -m uvicorn epidemic_core.dashboard.app:create_app --factory --host '$HOST' --port '$PORT' --no-access-log" >>"$LOGFILE" 2>&1 &
pid=$!
printf '%s\n' "$pid" >"$PIDFILE"
chown epidemicui:epidemicui "$PIDFILE" "$LOGFILE"
chmod 0600 "$PIDFILE" "$LOGFILE"
for _ in $(seq 1 60); do
  if dashboard_alive && dashboard_health; then exit 0; fi
  sleep 1
done
echo 'dashboard failed to become healthy' >&2
exit 1
