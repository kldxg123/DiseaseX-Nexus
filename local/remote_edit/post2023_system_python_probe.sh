#!/usr/bin/env bash
set -euo pipefail
ls -l /usr/bin/python3 /usr/bin/python3.10
/usr/bin/python3.10 --version
/usr/bin/python3.10 - <<'PY'
import importlib
import sys
print(sys.executable)
for name in ("sqlalchemy", "numpy", "pandas", "sklearn", "statsmodels", "torch"):
    try:
        m=importlib.import_module(name)
        print(name, getattr(m, "__version__", "present"))
    except BaseException as exc:
        print(name, "UNAVAILABLE", type(exc).__name__)
PY
