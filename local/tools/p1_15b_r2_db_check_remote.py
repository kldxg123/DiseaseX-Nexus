from __future__ import annotations

import json
import os

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


def main() -> None:
    url = os.environ.get("MIGRATION_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL/MIGRATION_DATABASE_URL is not set")
    if os.environ.get("POSTGRES_ADMIN_PASSWORD") and os.environ.get("POSTGRES_ADMIN_USER"):
        url = make_url(url).set(
            username=os.environ["POSTGRES_ADMIN_USER"],
            password=os.environ["POSTGRES_ADMIN_PASSWORD"],
        )

    engine = create_engine(url, hide_parameters=True)
    with engine.connect() as conn:
        conn.execute(text("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        try:
            alembic_tables = [
                dict(row)
                for row in conn.execute(
                    text(
                        "SELECT table_schema, table_name "
                        "FROM information_schema.tables "
                        "WHERE table_name = 'alembic_version' "
                        "ORDER BY table_schema"
                    )
                ).mappings()
            ]
            alembic_versions = {}
            for item in alembic_tables:
                schema = item["table_schema"]
                alembic_versions[schema] = conn.execute(
                    text(f'SELECT version_num FROM "{schema}".alembic_version LIMIT 1')
                ).scalar()
            result = {
                "transaction_read_only": conn.execute(text("SHOW transaction_read_only")).scalar(),
                "alembic_versions": alembic_versions,
                "don398_review_case_count": conn.execute(
                    text(
                        "SELECT count(*) FROM epidemic_discovery.source_review_cases "
                        "WHERE id = CAST(:id AS uuid)"
                    ),
                    {"id": "668e3dd5-cbdf-5e19-af8b-5360ef9e1af9"},
                ).scalar(),
                "don398_preview_rows": conn.execute(
                    text(
                        "SELECT count(*) FROM epidemic_discovery.source_preview_rows "
                        "WHERE candidate_version_id = CAST(:id AS uuid)"
                    ),
                    {"id": "2e71e03e-db7e-50b9-bc2d-ac514e7c728e"},
                ).scalar(),
                "trusted_cod_ebola_count": conn.execute(
                    text(
                        "SELECT count(*) FROM epidemic_p1_12.trusted_dashboard_series_v1 "
                        "WHERE country_alpha2 = 'CD' "
                        "AND lower(disease_name) LIKE '%ebola%'"
                    )
                ).scalar(),
            }
        finally:
            conn.execute(text("ROLLBACK"))

    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
