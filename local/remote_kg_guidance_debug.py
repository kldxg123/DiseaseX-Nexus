from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine

ROOT = Path("USER_HOME/projects/openclaw")
SECRETS = ROOT / "runtime/secrets/vm201_runtime.env"

for raw in SECRETS.read_text(encoding="utf-8").splitlines():
    line = raw.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    os.environ[key.strip()] = value.strip().strip('"').strip("'")

os.environ["PYTHONPATH"] = str(ROOT / "runtime/python-packages") + ":" + str(ROOT / "src")
from epidemic_core.forecast_model_kg.service import guidance_response  # noqa: E402

db_url = (
    "postgresql+psycopg://epidemic_dashboard_ro:"
    + os.environ["EPIDEMIC_ROLE_PASS"]
    + "@127.0.0.1:55432/epidemic_openclaw"
)
engine = create_engine(db_url)
try:
    out = guidance_response(engine, "COVID-19", "FRA", "cases_new", 4)
    print("PASS", out["resolved_disease"]["id"], len(out["recommended_model_families"]))
except Exception as exc:
    print("FAIL_REDACTED", type(exc).__name__, str(exc)[:300])
