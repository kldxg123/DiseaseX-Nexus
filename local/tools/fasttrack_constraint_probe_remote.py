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
    engine = sa.create_engine(url, hide_parameters=True)
    with engine.connect() as conn:
        payload = {
            "constraints": [
                dict(r)
                for r in conn.execute(
                    text(
                        """
                        SELECT conname, pg_get_constraintdef(oid) AS definition
                        FROM pg_constraint
                        WHERE conrelid='epidemic_p1_12.trusted_source_contracts'::regclass
                          AND conname LIKE '%authorization%'
                        ORDER BY conname
                        """
                    )
                ).mappings()
            ],
            "authorization_scope_values": [
                dict(r)
                for r in conn.execute(
                    text(
                        """
                        SELECT authorization_scope, count(*)::int AS count
                        FROM epidemic_p1_12.trusted_source_contracts
                        GROUP BY 1
                        ORDER BY 2 DESC, 1
                        """
                    )
                ).mappings()
            ],
        }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
