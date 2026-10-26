from __future__ import annotations

import json
import os

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import make_url


def main() -> None:
    raw = os.environ.get("MIGRATION_DATABASE_URL") or os.environ.get("DATABASE_URL") or os.environ.get("DASHBOARD_DATABASE_URL")
    if not raw:
        raise SystemExit("missing database url")
    url = make_url(raw)
    if os.environ.get("POSTGRES_ADMIN_USER") and os.environ.get("POSTGRES_ADMIN_PASSWORD"):
        url = url.set(username=os.environ["POSTGRES_ADMIN_USER"], password=os.environ["POSTGRES_ADMIN_PASSWORD"])
    engine = sa.create_engine(url, hide_parameters=True, future=True)
    issue_week = "2026-07-06"
    target_week = "2026-07-13"
    with engine.connect() as conn:
        conn.exec_driver_sql("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        conn.exec_driver_sql("SET LOCAL statement_timeout = '30s'")
        row = conn.execute(
            text(
                """
                SELECT period_start::text AS last_observation_week,
                       value_numeric::float AS last_observed_value,
                       disease_slug,
                       metric_code,
                       country_iso3
                FROM epidemic_p1_12.trusted_dashboard_series_v1
                WHERE granularity='week'
                  AND country_iso3='SGP'
                  AND metric_code='influenza_positive_detections'
                  AND value_numeric IS NOT NULL
                  AND period_start <= CAST(:issue_week AS date)
                ORDER BY period_start DESC
                LIMIT 1
                """
            ),
            {"issue_week": issue_week},
        ).mappings().first()
        readonly = conn.exec_driver_sql("SHOW transaction_read_only").scalar()
        conn.exec_driver_sql("ROLLBACK")
    payload = {
        "transaction_read_only": readonly,
        "issue_week": issue_week,
        "target_week": target_week,
        "expected_last_observation_week": "2026-07-06",
        "expected_last_observed_value": 38,
        "observed": dict(row) if row else None,
        "pass": bool(row and row["last_observation_week"] == "2026-07-06" and float(row["last_observed_value"]) == 38.0),
        "missing_to_zero_repaired": bool(row and float(row["last_observed_value"]) != 0.0),
        "business_database_writes": 0,
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
