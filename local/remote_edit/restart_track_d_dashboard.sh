#!/usr/bin/env bash
set -euo pipefail
cd USER_HOME/projects/openclaw
old="$(cat runtime/dashboard.pid 2>/dev/null || true)"
if [[ -n "$old" ]] && kill -0 "$old" 2>/dev/null; then
  kill "$old"
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    kill -0 "$old" 2>/dev/null || break
    sleep 1
  done
fi
nohup runtime/start_vm201_dashboard.sh > runtime/logs/dashboard_track_d_final_20260813.log 2>&1 &
echo "$!" > runtime/dashboard.pid
sleep 5
pid="$(cat runtime/dashboard.pid)"
kill -0 "$pid"
curl -fsS -o /tmp/track_d_health.json -w 'health=%{http_code}\n' http://127.0.0.1:8020/healthz
curl -fsS -o /tmp/track_d_ready.json -w 'ready=%{http_code}\n' http://127.0.0.1:8020/readyz
curl -fsS http://127.0.0.1:8020/forecast-model-knowledge-graph | grep -q 'kgQTrackD'
echo track_d_dom=PASS
