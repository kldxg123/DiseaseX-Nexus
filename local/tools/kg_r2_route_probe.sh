#!/usr/bin/env bash
set -euo pipefail
cd USER_HOME/projects/openclaw
grep -R -n -E 'forecast-model-knowledge-graph|kg/explorer|APIRouter|@app\.get|@router\.get' src/epidemic_core/dashboard | head -200 || true
