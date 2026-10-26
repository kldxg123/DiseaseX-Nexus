from __future__ import annotations

import json
import os
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import make_url


def main() -> None:
    project = Path(os.environ["PROJECT"]).resolve()
    raw_url = os.environ.get("MIGRATION_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not raw_url:
        raise SystemExit("DATABASE_URL_NOT_CONFIGURED")
    url = make_url(raw_url)
    if os.environ.get("POSTGRES_ADMIN_USER") and os.environ.get("POSTGRES_ADMIN_PASSWORD"):
        url = url.set(username=os.environ["POSTGRES_ADMIN_USER"], password=os.environ["POSTGRES_ADMIN_PASSWORD"])
    engine = sa.create_engine(url, hide_parameters=True, future=True)

    with engine.connect() as conn:
        conn.execute(text("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        conn.execute(text("SET LOCAL statement_timeout = '30s'"))
        ro = conn.execute(text("SHOW transaction_read_only")).scalar_one()
        alembic_tables = [
            dict(row)
            for row in conn.execute(
                text(
                    """
                    SELECT table_schema, table_name
                    FROM information_schema.tables
                    WHERE table_name = 'alembic_version'
                    ORDER BY table_schema
                    """
                )
            ).mappings()
        ]
        alembic: list[dict[str, object]] = []
        for table in alembic_tables:
            schema = table["table_schema"]
            rows = conn.execute(text(f'SELECT version_num FROM "{schema}".alembic_version ORDER BY version_num')).scalars().all()
            alembic.append({"schema": schema, "versions": rows})
        tables = [
            dict(row)
            for row in conn.execute(
                text(
                    """
                    SELECT table_schema, table_name, table_type
                    FROM information_schema.tables
                    WHERE table_schema NOT IN ('pg_catalog','information_schema')
                    ORDER BY table_schema, table_name
                    """
                )
            ).mappings()
        ]
        interesting_names = [
            name
            for name in (
                "diseases",
                "metric_definitions",
                "data_sources",
                "observations",
                "observation_versions",
                "source_contracts",
                "source_report_versions",
                "source_report_observations",
                "review_candidates",
                "review_cases",
                "forecast_runs",
                "forecast_predictions",
                "forecast_backtest_predictions",
                "forecast_training_snapshots",
                "forecast_latest_runs",
            )
        ]
        columns = [
            dict(row)
            for row in conn.execute(
                text(
                    """
                    SELECT table_schema, table_name, column_name, data_type, udt_name, is_nullable,
                           column_default
                    FROM information_schema.columns
                    WHERE table_schema NOT IN ('pg_catalog','information_schema')
                      AND (
                        table_name = ANY(:names)
                        OR table_name ILIKE '%forecast%'
                        OR table_name ILIKE '%trusted%'
                        OR table_name ILIKE '%observation%'
                        OR table_name ILIKE '%source%'
                        OR table_name ILIKE '%disease%'
                        OR table_name ILIKE '%metric%'
                      )
                    ORDER BY table_schema, table_name, ordinal_position
                    """
                ),
                {"names": interesting_names},
            ).mappings()
        ]
        counts = {}
        for row in tables:
            schema = row["table_schema"]
            name = row["table_name"]
            if (
                "forecast" in name
                or "observation" in name
                or "source" in name
                or name in {"diseases", "metric_definitions", "data_sources"}
            ):
                try:
                    counts[f"{schema}.{name}"] = int(
                        conn.execute(text(f'SELECT count(*) FROM "{schema}"."{name}"')).scalar_one()
                    )
                except Exception as exc:  # pragma: no cover - introspection only
                    counts[f"{schema}.{name}"] = f"ERROR:{type(exc).__name__}"
        runs = [
            dict(row)
            for row in conn.execute(
                text(
                    """
                    SELECT id::text, disease_slug, target_metric, status, validation_level,
                           champion_model, cutoff_week::text, fallback_reason
                    FROM epidemic_forecast.forecast_runs
                    ORDER BY published_at DESC NULLS LAST, created_at DESC
                    LIMIT 20
                    """
                )
            ).mappings()
        ] if any(t["table_schema"] == "epidemic_forecast" and t["table_name"] == "forecast_runs" for t in tables) else []
        conn.execute(text("ROLLBACK"))

    def rel_files(patterns: list[str], limit: int = 220) -> list[str]:
        out: list[str] = []
        for pattern in patterns:
            out.extend(str(p.relative_to(project)) for p in project.glob(pattern) if p.is_file())
        return sorted(set(out))[:limit]

    result = {
        "project": str(project),
        "read_only": ro,
        "alembic": alembic,
        "tables": tables,
        "columns": columns,
        "counts": counts,
        "forecast_runs": runs,
        "files": {
            "migrations": rel_files(["migrations/versions/*.py"], 120),
            "dashboard": rel_files(["src/epidemic_core/dashboard/**/*.py", "src/epidemic_core/dashboard/**/*"], 160),
            "api": rel_files(["src/epidemic_core/api/**/*.py"], 160),
            "forecast": rel_files(["src/epidemic_core/**/*forecast*.py", "src/epidemic_core/forecasting/**/*.py"], 160),
            "sources": rel_files(["src/epidemic_core/**/*source*.py", "reference/*.json", "contracts/**/*.json"], 220),
            "tests": rel_files(["tests/**/*.py"], 160),
        },
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
