#!/usr/bin/env bash
set -euo pipefail
cd USER_HOME/projects/openclaw
.venv/bin/python - <<'PY'
import importlib

for name in ("statsmodels", "sklearn", "numpy", "pandas", "torch"):
    try:
        module = importlib.import_module(name)
        print(f"{name}={getattr(module, '__version__', 'unknown')}")
    except Exception as exc:
        print(f"{name}=UNAVAILABLE:{type(exc).__name__}")
PY
git rev-parse HEAD
git status --porcelain | head -n 20
