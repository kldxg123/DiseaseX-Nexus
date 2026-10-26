from __future__ import annotations

import json
import os
from sqlalchemy import text

from epidemic_core.config import Settings
from epidemic_core.db.engine import create_app_engine

url = os.environ.get("APP_DATABASE_URL") or os.environ.get("DASHBOARD_DATABASE_URL")
engine = create_app_engine(Settings(APP_DATABASE_URL=url))
with engine.connect() as conn:
    tx = conn.begin()
    conn.execute(text("SET TRANSACTION READ ONLY"))
    conn.execute(text("SET LOCAL statement_timeout='30s'"))
    rows = conn.execute(text("""
        SELECT c.snapshot_id::text, c.country_alpha2, c.country_name, c.disease_slug,
               c.source_key, c.requested_start, c.requested_end, c.observation_count,
               c.snapshot_sha256, c.granularities, c.snapshot_created_at,
               count(*) FILTER (WHERE o.metric_code='cases_new') AS cases_rows,
               min(o.period_start) FILTER (WHERE o.metric_code='cases_new') AS first_case,
               max(o.period_start) FILTER (WHERE o.metric_code='cases_new') AS last_case,
               count(DISTINCT o.period_start) FILTER (WHERE o.metric_code='cases_new') AS distinct_weeks
        FROM epidemic.dashboard_snapshot_catalog_v1 c
        LEFT JOIN epidemic.dashboard_observation_series_v1 o USING(snapshot_id)
        WHERE c.country_alpha2='BR' AND c.disease_slug='dengue'
        GROUP BY c.snapshot_id, c.country_alpha2, c.country_name, c.disease_slug,
                 c.source_key, c.requested_start, c.requested_end, c.observation_count,
                 c.snapshot_sha256, c.granularities, c.snapshot_created_at
        ORDER BY c.snapshot_created_at DESC
    """)).mappings().all()
    tx.rollback()
print(json.dumps([dict(r) for r in rows], default=str, ensure_ascii=False, indent=2))
