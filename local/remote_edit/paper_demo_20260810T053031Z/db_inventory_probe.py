#!/usr/bin/env python3
"""Read-only schema inventory for the paper screenshot task."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

import psycopg


DSN = (
    "host=USER_HOME/projects/openclaw/runtime/postgres-run "
    "port=55432 dbname=epidemic_openclaw user=postgres"
)

PATTERNS = (
    "observ",
    "snapshot",
    "forecast",
    "recommend",
    "execution",
    "profile",
    "review",
    "promotion",
    "active",
    "graph",
    "claim",
    "discovery",
    "query",
    "candidate",
    "publication",
    "decision",
)


def main() -> None:
    out: dict[str, object] = {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "transaction": {},
        "alembic_versions": [],
        "tables": [],
    }
    with psycopg.connect(DSN) as conn:
        conn.execute("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        conn.execute("SET LOCAL statement_timeout = '30s'")
        conn.execute("SET LOCAL idle_in_transaction_session_timeout = '60s'")
        out["transaction"] = {
            "transaction_read_only": conn.execute("SHOW transaction_read_only").fetchone()[0],
            "transaction_isolation": conn.execute("SHOW transaction_isolation").fetchone()[0],
        }
        if conn.execute(
            "SELECT to_regclass('public.alembic_version') IS NOT NULL"
        ).fetchone()[0]:
            out["alembic_versions"] = [
                r[0] for r in conn.execute("SELECT version_num FROM public.alembic_version ORDER BY 1")
            ]
        tables = conn.execute(
            """
            SELECT table_schema, table_name
            FROM information_schema.tables
            WHERE table_type = 'BASE TABLE'
              AND table_schema NOT IN ('pg_catalog', 'information_schema')
            ORDER BY table_schema, table_name
            """
        ).fetchall()
        for schema, table in tables:
            lname = table.lower()
            if not any(p in lname for p in PATTERNS):
                continue
            cols = [
                {"name": r[0], "type": r[1], "nullable": r[2] == "YES"}
                for r in conn.execute(
                    """
                    SELECT column_name, data_type, is_nullable
                    FROM information_schema.columns
                    WHERE table_schema = %s AND table_name = %s
                    ORDER BY ordinal_position
                    """,
                    (schema, table),
                )
            ]
            count = conn.execute(
                psycopg.sql.SQL("SELECT count(*) FROM {}.{}").format(
                    psycopg.sql.Identifier(schema), psycopg.sql.Identifier(table)
                )
            ).fetchone()[0]
            out["tables"].append(
                {"schema": schema, "table": table, "row_count": count, "columns": cols}
            )
        conn.execute("ROLLBACK")
    canonical = json.dumps(out, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    out["inventory_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
