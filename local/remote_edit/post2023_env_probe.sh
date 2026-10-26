#!/usr/bin/env bash
set -euo pipefail
cd USER_HOME/projects/openclaw
echo "python_candidates"
for p in \
  /usr/bin/python3 \
  /usr/bin/python3.10 \
  /usr/local/bin/python3 \
  USER_HOME/.local/bin/python \
  USER_HOME/miniconda3/bin/python \
  USER_HOME/anaconda3/bin/python \
  /opt/conda/bin/python \
  USER_HOME/.conda/envs/*/bin/python \
  USER_HOME/projects/openclaw/.venv/bin/python; do
  if [ -x "$p" ]; then
    "$p" - <<'PY' 2>/dev/null || true
import importlib
mods=[]
for n in ("statsmodels", "sklearn", "numpy", "pandas", "torch"):
    try:
        m=importlib.import_module(n); mods.append(f"{n}={getattr(m,'__version__','?')}")
    except BaseException:
        pass
import sys
if mods: print(sys.executable+" "+" ".join(mods))
PY
  fi
done
find USER_HOME USER_HOME/autodl-tmp -maxdepth 5 -type f -path '*/bin/python*' 2>/dev/null | head -n 120 | while read -r p; do
  [ -x "$p" ] || continue
  case "$p" in *-config) continue;; esac
  "$p" - <<'PY' 2>/dev/null || true
import importlib
mods=[]
for n in ("statsmodels", "sklearn", "numpy", "pandas", "torch"):
    try:
        m=importlib.import_module(n); mods.append(f"{n}={getattr(m,'__version__','?')}")
    except BaseException:
        pass
import sys
if mods: print(sys.executable+" "+" ".join(mods))
PY
done
echo "process_python"
ps -eo pid=,args= | grep -E 'dashboard|uvicorn|gunicorn' | grep -v grep | head -n 20 || true
