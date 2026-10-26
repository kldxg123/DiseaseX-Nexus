from __future__ import annotations

import json
import os

import psycopg
from psycopg import sql


TERMS = ("ukhsa-dashboard", "influenza_testing_positivityByWeek")


with psycopg.connect(
    host="127.0.0.1",
    port=55432,
    dbname="epidemic_openclaw",
    user="epidemic_dashboard_ro",
    password=os.environ["EPIDEMIC_ROLE_PASS"],
) as conn:
    conn.execute("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
    conn.execute("SET LOCAL statement_timeout = '30s'")
    conn.execute("SET LOCAL idle_in_transaction_session_timeout = '60s'")
    readonly = conn.execute("SHOW transaction_read_only").fetchone()[0]
    columns = conn.execute(
        """
        SELECT table_schema, table_name, column_name
        FROM information_schema.columns
        WHERE table_schema IN ('epidemic', 'epidemic_kg')
          AND data_type IN ('text', 'character varying', 'json', 'jsonb')
        ORDER BY table_schema, table_name, ordinal_position
        """
    ).fetchall()
    matches: list[dict[str, object]] = []
    checked = 0
    for schema, table, column in columns:
        checked += 1
        query = sql.SQL(
            "SELECT count(*) FROM {}.{} WHERE lower(coalesce({}::text, '')) LIKE %s"
        ).format(sql.Identifier(schema), sql.Identifier(table), sql.Identifier(column))
        for term in TERMS:
            try:
                count = conn.execute(query, (f"%{term.lower()}%",)).fetchone()[0]
            except (psycopg.errors.InsufficientPrivilege, psycopg.errors.UndefinedTable):
                conn.rollback()
                conn.execute("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                conn.execute("SET LOCAL statement_timeout = '30s'")
                break
            if count:
                matches.append(
                    {
                        "schema": schema,
                        "table": table,
                        "column": column,
                        "term": term,
                        "count": count,
                    }
                )
    conn.rollback()

print(
    json.dumps(
        {
            "transaction_read_only": readonly,
            "columns_checked": checked,
            "match_count": sum(int(item["count"]) for item in matches),
            "matches": matches,
        },
        sort_keys=True,
    )
)
