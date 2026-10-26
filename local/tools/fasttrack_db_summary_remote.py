from __future__ import annotations

import json
import os

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import make_url


def main() -> None:
    raw = os.environ.get("MIGRATION_DATABASE_URL") or os.environ.get("DATABASE_URL") or os.environ.get("DASHBOARD_DATABASE_URL")
    url = make_url(raw)
    if os.environ.get("POSTGRES_ADMIN_USER") and os.environ.get("POSTGRES_ADMIN_PASSWORD"):
        url = url.set(username=os.environ["POSTGRES_ADMIN_USER"], password=os.environ["POSTGRES_ADMIN_PASSWORD"])
    engine = sa.create_engine(url, hide_parameters=True, future=True)
    with engine.connect() as conn:
        rows = [
            dict(r)
            for r in conn.execute(
                text(
                    """
                    SELECT disease_slug, metric_code, count(*)::int AS rows,
                           count(DISTINCT country_iso3)::int AS countries,
                           min(period_start)::text AS date_min,
                           max(period_start)::text AS date_max,
                           count(*) FILTER (WHERE value_numeric > 0)::int AS positive_rows
                    FROM epidemic_p1_12.trusted_dashboard_series_v1
                    WHERE granularity='week'
                      AND metric_code IN ('cases_new','influenza_positive_detections')
                    GROUP BY 1,2
                    ORDER BY
                      CASE
                        WHEN disease_slug ILIKE '%dengue%' AND metric_code='cases_new' THEN 0
                        WHEN disease_slug ILIKE '%covid%' AND metric_code='cases_new' THEN 1
                        WHEN disease_slug ILIKE '%ebola%' AND metric_code='cases_new' THEN 2
                        WHEN disease_slug ILIKE '%influenza%' THEN 3
                        ELSE 9
                      END,
                      rows DESC
                    """
                )
            ).mappings()
        ]
        forecast = [dict(r) for r in conn.execute(text("SELECT id::text,status,validation_level,disease_slug,target_metric,cutoff_week::text,champion_model,fallback_reason FROM epidemic.forecast_runs ORDER BY created_at DESC LIMIT 5")).mappings()]
    print(json.dumps({"weekly_metric_summary": rows, "forecast_runs": forecast}, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
