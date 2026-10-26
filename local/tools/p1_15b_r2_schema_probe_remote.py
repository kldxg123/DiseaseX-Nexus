from __future__ import annotations

import json
import os
from typing import Any

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import make_url


def db_url():
    url = os.environ["MIGRATION_DATABASE_URL"]
    if os.environ.get("POSTGRES_ADMIN_PASSWORD") and os.environ.get("POSTGRES_ADMIN_USER"):
        url = make_url(url).set(username=os.environ["POSTGRES_ADMIN_USER"], password=os.environ["POSTGRES_ADMIN_PASSWORD"])
    return url


engine = sa.create_engine(db_url(), hide_parameters=True, future=True)
with engine.connect() as conn:
    conn.execute(text("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY"))
    tables = [
        dict(r)
        for r in conn.execute(
            text(
                """
                SELECT table_schema, table_name
                FROM information_schema.tables
                WHERE table_schema='epidemic_discovery'
                  AND (table_name ILIKE '%run%' OR table_name ILIKE '%request%' OR table_name ILIKE '%candidate%' OR table_name ILIKE '%review%' OR table_name ILIKE '%search%' OR table_name ILIKE '%preview%')
                ORDER BY table_schema, table_name
                """
            )
        )
        .mappings()
        .all()
    ]
    columns: dict[str, Any] = {}
    for t in tables:
        key = f"{t['table_schema']}.{t['table_name']}"
        columns[key] = [
            dict(r)
            for r in conn.execute(
                text(
                    """
                    SELECT column_name, data_type, is_nullable, column_default
                    FROM information_schema.columns
                    WHERE table_schema=:s AND table_name=:t
                    ORDER BY ordinal_position
                    """
                ),
                {"s": t["table_schema"], "t": t["table_name"]},
            )
            .mappings()
            .all()
        ]
    constraints = [
        dict(r)
        for r in conn.execute(
            text(
                """
                SELECT
                  tc.table_schema, tc.table_name, tc.constraint_name, tc.constraint_type,
                  kcu.column_name, ccu.table_schema AS foreign_table_schema,
                  ccu.table_name AS foreign_table_name, ccu.column_name AS foreign_column_name
                FROM information_schema.table_constraints tc
                LEFT JOIN information_schema.key_column_usage kcu
                  ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
                LEFT JOIN information_schema.constraint_column_usage ccu
                  ON ccu.constraint_name = tc.constraint_name AND ccu.table_schema = tc.table_schema
                WHERE tc.table_schema='epidemic_discovery'
                  AND tc.table_name IN ('source_candidates','source_candidate_versions','source_preview_rows','source_review_cases','discovery_runs','discovery_requests','search_results','discovery_run_candidate_links')
                ORDER BY tc.table_name, tc.constraint_type, tc.constraint_name, kcu.ordinal_position
                """
            )
        )
        .mappings()
        .all()
    ]
    sample_counts: dict[str, int] = {}
    for name in ["discovery_runs", "discovery_requests", "search_results", "source_candidates", "source_candidate_versions", "source_review_cases", "source_preview_rows"]:
        exists = conn.execute(text("SELECT to_regclass(:r) IS NOT NULL"), {"r": f"epidemic_discovery.{name}"}).scalar_one()
        if exists:
            sample_counts[f"epidemic_discovery.{name}"] = int(conn.execute(text(f'SELECT count(*)::bigint FROM epidemic_discovery."{name}"')).scalar_one())
    distinct_states: dict[str, Any] = {}
    for table, col in [
        ("discovery_runs", "state"),
        ("source_candidates", "status"),
        ("source_review_cases", "state"),
    ]:
        exists = conn.execute(text("SELECT to_regclass(:r) IS NOT NULL"), {"r": f"epidemic_discovery.{table}"}).scalar_one()
        if exists:
            distinct_states[f"epidemic_discovery.{table}.{col}"] = [
                dict(r)
                for r in conn.execute(text(f'SELECT "{col}" AS value, count(*)::bigint AS rows FROM epidemic_discovery."{table}" GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 20'))
                .mappings()
                .all()
            ]
    conn.execute(text("ROLLBACK"))
print(json.dumps({"tables": tables, "columns": columns, "constraints": constraints, "counts": sample_counts, "distinct_states": distinct_states}, ensure_ascii=False, indent=2, sort_keys=True))
