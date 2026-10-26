from __future__ import annotations

import json
import os

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


def db_url():
    url = os.environ["MIGRATION_DATABASE_URL"]
    if os.environ.get("POSTGRES_ADMIN_PASSWORD") and os.environ.get("POSTGRES_ADMIN_USER"):
        url = make_url(url).set(username=os.environ["POSTGRES_ADMIN_USER"], password=os.environ["POSTGRES_ADMIN_PASSWORD"])
    return url


def cols(conn, schema: str, table: str):
    return [
        dict(r)
        for r in conn.execute(
            text(
                """
                SELECT column_name,data_type,is_nullable,column_default
                FROM information_schema.columns
                WHERE table_schema=:schema AND table_name=:table
                ORDER BY ordinal_position
                """
            ),
            {"schema": schema, "table": table},
        ).mappings()
    ]


def main() -> None:
    engine = create_engine(db_url(), hide_parameters=True)
    tables = [
        ("epidemic_p1_12", "trusted_source_contracts"),
        ("epidemic_p1_12", "trusted_fetch_artifacts"),
        ("epidemic_p1_12", "trusted_observation_lineage"),
        ("epidemic_p1_12", "trusted_snapshot_lineage"),
        ("epidemic", "data_sources"),
        ("epidemic", "query_requests"),
        ("epidemic", "retrieval_runs"),
        ("epidemic", "source_fetches"),
        ("epidemic", "raw_artifacts"),
        ("epidemic", "normalized_observations"),
        ("epidemic", "quality_assessments"),
        ("epidemic", "dataset_snapshots"),
        ("epidemic", "dataset_snapshot_observations"),
    ]
    with engine.connect() as conn:
        conn.execute(text("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        try:
            contracts = [
                dict(r)
                for r in conn.execute(
                    text(
                        """
                        SELECT id, source_key, contract_version, publisher, canonical_url,
                               terms_url, trust_tier, active, terms_artifact_sha256
                        FROM epidemic_p1_12.trusted_source_contracts
                        ORDER BY source_key, contract_version
                        """
                    )
                ).mappings()
            ]
            counts = {
                f"{schema}.{table}": int(
                    conn.execute(text(f'SELECT count(*) FROM "{schema}"."{table}"')).scalar_one()
                )
                for schema, table in tables
            }
            dashboard_cols = cols(conn, "epidemic_p1_12", "trusted_dashboard_series_v1")
            ebola_metrics = [
                dict(r)
                for r in conn.execute(
                    text(
                        """
                        SELECT metric_code, count(*) rows, min(period_start) min_period, max(period_end) max_period
                        FROM epidemic_p1_12.trusted_dashboard_series_v1
                        WHERE disease_slug='ebola-disease' AND country_iso3='COD'
                        GROUP BY metric_code ORDER BY metric_code
                        """
                    )
                ).mappings()
            ]
            print(
                json.dumps(
                    {
                        "contracts": contracts,
                        "counts": counts,
                        "columns": {f"{s}.{t}": cols(conn, s, t) for s, t in tables},
                        "dashboard_columns": dashboard_cols,
                        "cod_ebola_metrics": ebola_metrics,
                    },
                    default=str,
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
        finally:
            conn.execute(text("ROLLBACK"))


if __name__ == "__main__":
    main()
