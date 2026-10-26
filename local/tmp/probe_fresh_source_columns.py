from __future__ import annotations

import json
import os

import psycopg


with psycopg.connect(
    host="127.0.0.1",
    port=55432,
    dbname="epidemic_openclaw",
    user="epidemic_dashboard_ro",
    password=os.environ["EPIDEMIC_ROLE_PASS"],
) as conn:
    conn.execute("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
    rows = conn.execute(
        """
        SELECT table_schema, table_name, column_name, data_type
        FROM information_schema.columns
        WHERE (table_schema, table_name) IN (
          ('epidemic_p1_12', 'trusted_dashboard_series_v1'),
          ('epidemic', 'normalized_observations'),
          ('epidemic', 'countries')
        )
        ORDER BY table_schema, table_name, ordinal_position
        """
    ).fetchall()
    print(json.dumps([{"schema": r[0], "table": r[1], "column": r[2], "type": r[3]} for r in rows]))
    conn.rollback()
