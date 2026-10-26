#!/usr/bin/env bash
set -euo pipefail
cd USER_HOME/projects/openclaw
echo "paper_runtime"
if [ -f reports/paper_comparison_aiken_20260813/comparison.json ]; then
  python3 - <<'PY'
import json
p="reports/paper_comparison_aiken_20260813/comparison.json"
d=json.load(open(p, encoding="utf-8"))
print(json.dumps({"runtime": d.get("paper", {}).get("reconstruction_runtime"), "data": d.get("data", {})}, sort_keys=True))
PY
fi
echo "cached_lightgbm"
find USER_HOME/.cache USER_HOME/miniconda3/pkgs -iname '*lightgbm*' -o -iname '*xgboost*' 2>/dev/null | head -n 80 || true
echo "db_loader_refs"
grep -R -l -E 'create_engine|DATABASE_URL|database_url' src/epidemic_core 2>/dev/null | head -n 80
