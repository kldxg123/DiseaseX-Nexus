from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import make_url


TABLES = [
    ("epidemic", "diseases"),
    ("epidemic", "disease_aliases"),
    ("epidemic", "metric_definitions"),
    ("epidemic", "data_sources"),
    ("epidemic", "raw_artifacts"),
    ("epidemic", "retrieval_runs"),
    ("epidemic", "retrieval_run_events"),
    ("epidemic", "normalization_runs"),
    ("epidemic", "normalized_observations"),
    ("epidemic", "quality_assessments"),
    ("epidemic", "dataset_snapshots"),
    ("epidemic", "dataset_snapshot_observations"),
    ("epidemic", "dashboard_history_catalog_v2"),
    ("epidemic", "dashboard_history_series_v2"),
    ("epidemic", "dashboard_catalog_options_v2"),
    ("epidemic", "forecast_runs"),
    ("epidemic", "forecast_values"),
    ("epidemic", "forecast_history_points"),
    ("epidemic", "forecast_fold_metrics"),
    ("epidemic", "forecast_artifacts"),
    ("epidemic", "latest_published_forecasts_v1"),
    ("epidemic_p1_12", "trusted_source_contracts"),
    ("epidemic_p1_12", "trusted_fetch_artifacts"),
    ("epidemic_p1_12", "trusted_observation_lineage"),
    ("epidemic_p1_12", "trusted_snapshot_lineage"),
    ("epidemic_p1_12", "trusted_dashboard_catalog_v1"),
    ("epidemic_p1_12", "trusted_dashboard_series_v1"),
]


def main() -> None:
    project = Path(os.environ["PROJECT"]).resolve()
    raw_url = os.environ.get("MIGRATION_DATABASE_URL") or os.environ.get("DATABASE_URL")
    url = make_url(raw_url)
    if os.environ.get("POSTGRES_ADMIN_USER") and os.environ.get("POSTGRES_ADMIN_PASSWORD"):
        url = url.set(username=os.environ["POSTGRES_ADMIN_USER"], password=os.environ["POSTGRES_ADMIN_PASSWORD"])
    engine = sa.create_engine(url, hide_parameters=True, future=True)
    with engine.connect() as conn:
        conn.execute(text("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        conn.execute(text("SET LOCAL statement_timeout = '30s'"))
        cols: dict[str, list[dict[str, object]]] = {}
        constraints: dict[str, list[dict[str, object]]] = {}
        indexes: dict[str, list[dict[str, object]]] = {}
        counts: dict[str, int | str] = {}
        for schema, table in TABLES:
            key = f"{schema}.{table}"
            exists = conn.execute(text("SELECT to_regclass(:name)"), {"name": key}).scalar_one()
            if not exists:
                cols[key] = []
                counts[key] = "missing"
                continue
            cols[key] = [
                dict(r)
                for r in conn.execute(
                    text(
                        """
                        SELECT column_name, data_type, udt_name, is_nullable, column_default
                        FROM information_schema.columns
                        WHERE table_schema=:schema AND table_name=:table
                        ORDER BY ordinal_position
                        """
                    ),
                    {"schema": schema, "table": table},
                ).mappings()
            ]
            constraints[key] = [
                dict(r)
                for r in conn.execute(
                    text(
                        """
                        SELECT tc.constraint_name, tc.constraint_type, kcu.column_name
                        FROM information_schema.table_constraints tc
                        LEFT JOIN information_schema.key_column_usage kcu
                          ON kcu.constraint_schema=tc.constraint_schema
                         AND kcu.constraint_name=tc.constraint_name
                         AND kcu.table_schema=tc.table_schema
                         AND kcu.table_name=tc.table_name
                        WHERE tc.table_schema=:schema AND tc.table_name=:table
                        ORDER BY tc.constraint_name, kcu.ordinal_position
                        """
                    ),
                    {"schema": schema, "table": table},
                ).mappings()
            ]
            indexes[key] = [
                dict(r)
                for r in conn.execute(
                    text(
                        """
                        SELECT indexname, indexdef
                        FROM pg_indexes
                        WHERE schemaname=:schema AND tablename=:table
                        ORDER BY indexname
                        """
                    ),
                    {"schema": schema, "table": table},
                ).mappings()
            ]
            try:
                counts[key] = int(conn.execute(text(f'SELECT count(*) FROM "{schema}"."{table}"')).scalar_one())
            except Exception as exc:
                counts[key] = f"ERROR:{type(exc).__name__}"
        refs = {}
        for query_name, sql in {
            "diseases": "SELECT slug, canonical_name, catalog_version, active FROM epidemic.diseases ORDER BY slug",
            "metrics": "SELECT metric_code, display_name, unit, direction, catalog_version FROM epidemic.metric_definitions ORDER BY metric_code",
            "sources": "SELECT source_key, display_name, authority, base_url, license_expression, enabled FROM epidemic.data_sources ORDER BY source_key",
            "trusted_contracts": "SELECT contract_key, source_key, publisher, license_expression, active FROM epidemic_p1_12.trusted_source_contracts ORDER BY contract_key",
            "dashboard_catalog_sample": "SELECT * FROM epidemic.dashboard_history_catalog_v2 ORDER BY disease_slug, source_key LIMIT 30",
            "trusted_catalog_sample": "SELECT * FROM epidemic_p1_12.trusted_dashboard_catalog_v1 ORDER BY disease_slug, source_key LIMIT 30",
        }.items():
            try:
                refs[query_name] = [dict(r) for r in conn.execute(text(sql)).mappings()]
            except Exception as exc:
                refs[query_name] = {"error": type(exc).__name__, "detail": str(exc)[:200]}
        conn.execute(text("ROLLBACK"))

    rg_patterns = [
        "def .*forecast|forecast_runs|forecast_values|latest_published_forecasts",
        "dashboard_history_catalog_v2|dashboard_history_series_v2|trusted_dashboard",
        "normalized_observations|dataset_snapshots|raw_artifacts|data_sources",
    ]
    grep: dict[str, str] = {}
    for pattern in rg_patterns:
        if shutil.which("rg"):
            proc = subprocess.run(["rg", "-n", pattern, "src", "migrations", "tests"], cwd=project, text=True, capture_output=True, timeout=30)
        else:
            proc = subprocess.run(
                ["grep", "-R", "-n", "-E", pattern, "src", "migrations", "tests"],
                cwd=project,
                text=True,
                capture_output=True,
                timeout=30,
            )
        grep[pattern] = "\n".join(proc.stdout.splitlines()[:80])
    result = {
        "project": str(project),
        "git_head": subprocess.run(["git", "rev-parse", "HEAD"], cwd=project, text=True, capture_output=True, timeout=20).stdout.strip(),
        "columns": cols,
        "constraints": constraints,
        "indexes": indexes,
        "counts": counts,
        "refs": refs,
        "grep": grep,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
