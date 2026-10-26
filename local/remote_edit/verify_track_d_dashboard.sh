#!/usr/bin/env bash
set -euo pipefail
cd USER_HOME/projects/openclaw
pid="$(cat runtime/dashboard.pid)"
kill -0 "$pid"
python3 /tmp/verify_track_d_dashboard.py
grep -q 'kgQTrackD' src/epidemic_core/forecast_model_kg/dashboard_app.py
echo track_d_server_template=PASS
