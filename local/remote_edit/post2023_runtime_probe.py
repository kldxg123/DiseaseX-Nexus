from __future__ import annotations

import importlib
import json
from pathlib import Path

mods = {}
for name in ("lightgbm", "xgboost", "pywt", "psycopg", "sqlalchemy"):
    try:
        module = importlib.import_module(name)
        mods[name] = getattr(module, "__version__", "present")
    except Exception as exc:
        mods[name] = f"UNAVAILABLE:{type(exc).__name__}"

root = Path("USER_HOME/projects/openclaw")
files = []
for base in (root / "storage", root / "reports", root / "experiments"):
    if not base.exists():
        continue
    for p in base.rglob("*"):
        if p.is_file() and any(k in p.name.lower() for k in ("infodengue", "dengue", "profile")):
            files.append(str(p.relative_to(root)))
            if len(files) >= 200:
                break
    if len(files) >= 200:
        break
print(json.dumps({"modules": mods, "files": files}, ensure_ascii=False, indent=2))
