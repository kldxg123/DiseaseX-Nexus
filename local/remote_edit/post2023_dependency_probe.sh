#!/usr/bin/env bash
set -euo pipefail
for p in \
  USER_HOME/miniconda3/envs/biomni_e1/bin/python \
  USER_HOME/miniconda3/envs/bio_env_py310/bin/python \
  USER_HOME/miniconda3/bin/python
do
  printf 'PY=%s\n' "$p"
  PYTHONPATH=USER_HOME/projects/openclaw/src:USER_HOME/projects/openclaw/runtime/python-packages \
    "$p" -c 'import importlib.util as u; print("pywt", bool(u.find_spec("pywt")), "sklearn", bool(u.find_spec("sklearn")), "pandas", bool(u.find_spec("pandas")), "torch", bool(u.find_spec("torch")))' || true
done
