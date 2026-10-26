from __future__ import annotations

import json
from sqlalchemy import inspect, text

from epidemic_core.config import Settings
from epidemic_core.db.engine import create_app_engine

import os

database_url = os.environ.get("APP_DATABASE_URL") or os.environ.get("DASHBOARD_DATABASE_URL")
engine = create_app_engine(Settings(APP_DATABASE_URL=database_url))
out = {}
with engine.connect() as conn:
    tx = conn.begin()
    conn.execute(text("SET TRANSACTION READ ONLY"))
    conn.execute(text("SET LOCAL statement_timeout = '30s'"))
    insp = inspect(conn)
    for schema, table in (
        ("epidemic", "dashboard_observation_series_v1"),
        ("epidemic", "dashboard_snapshot_catalog_v1"),
        ("epidemic_kg", "kg_data_snapshots"),
    ):
        if insp.has_table(table, schema=schema):
            out[f"{schema}.{table}"] = [{"name": c["name"], "type": str(c["type"])} for c in insp.get_columns(table, schema=schema)]
    rows = conn.execute(text("""
        SELECT table_schema, table_name, table_type
        FROM information_schema.tables
        WHERE table_name ILIKE ANY (ARRAY['%observation%', '%snapshot%', '%source%'])
        ORDER BY table_schema, table_name
    """)).mappings().all()
    out["candidate_tables"] = [dict(r) for r in rows]
    tx.rollback()
print(json.dumps(out, ensure_ascii=False, indent=2))
