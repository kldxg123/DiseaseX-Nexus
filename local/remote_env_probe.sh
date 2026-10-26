#!/usr/bin/env bash
set -euo pipefail
for py in \
  "$HOME/Ebola/venv/bin/python" \
  "$HOME/miniconda3/envs/bio_env_py310/bin/python" \
  "$HOME/miniconda3/envs/biomni_e1/bin/python" \
  "$HOME/projects/openclaw-fresh-opt-20260814/.venv_freshopt/bin/python"
do
  echo "PY=${py}"
  "$py" - <<'PY'
import importlib.util

modules = ["numpy", "pandas", "sklearn", "statsmodels", "xgboost", "scipy", "pywt"]
print(" ".join(f"{name}={int(importlib.util.find_spec(name) is not None)}" for name in modules))
PY
done
