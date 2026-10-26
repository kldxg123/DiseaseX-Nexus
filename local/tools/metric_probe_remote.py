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


def main() -> None:
    engine = create_engine(db_url(), hide_parameters=True)
    with engine.connect() as conn:
        conn.execute(text("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        try:
            metric_columns = [
                r[0]
                for r in conn.execute(
                    text(
                        """
                        SELECT column_name
                        FROM information_schema.columns
                        WHERE table_schema='epidemic' AND table_name='metric_definitions'
                        ORDER BY ordinal_position
                        """
                    )
                )
            ]
            select_cols = ", ".join(f'"{c}"' for c in metric_columns)
            rows = [
                dict(r)
                for r in conn.execute(
                    text(
                        f"""
                        SELECT {select_cols}
                        FROM epidemic.metric_definitions
                        WHERE metric_code ILIKE '%ebola%' OR metric_code ILIKE '%case%' OR metric_code ILIKE '%death%'
                        ORDER BY metric_code
                        """
                    )
                ).mappings()
            ]
            print(json.dumps({"columns": metric_columns, "rows": rows}, default=str, ensure_ascii=False, sort_keys=True))
        finally:
            conn.execute(text("ROLLBACK"))


if __name__ == "__main__":
    main()
