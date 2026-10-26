from __future__ import annotations

import json
import os

import psycopg


result: dict[str, object] = {}
with psycopg.connect(
    host="127.0.0.1",
    port=55432,
    dbname="epidemic_openclaw",
    user="epidemic_dashboard_ro",
    password=os.environ["EPIDEMIC_ROLE_PASS"],
) as conn:
    with conn.cursor() as cur:
        cur.execute("begin transaction isolation level repeatable read read only")
        cur.execute("set local statement_timeout = '30s'")
        cur.execute("set local idle_in_transaction_session_timeout = '60s'")
        cur.execute("show transaction_read_only")
        result["transaction_read_only"] = cur.fetchone()[0]

        cur.execute(
            """
            select table_schema, table_name
            from information_schema.tables
            where table_schema in ('epidemic', 'epidemic_kg')
              and (
                table_name ilike '%observation%'
                or table_name ilike '%source%'
                or table_name ilike '%snapshot%'
                or table_name ilike '%forecast%'
                or table_name ilike '%heldout%'
              )
            order by table_schema, table_name
            """
        )
        result["relevant_tables"] = [f"{schema}.{table}" for schema, table in cur.fetchall()]

        wanted = (
            "normalized_observations",
            "data_sources",
            "kg_qwen_heldout_registry",
            "kg_data_snapshots",
            "forecast_runs",
            "forecast_values",
        )
        cur.execute(
            """
            select table_schema, table_name, column_name, data_type
            from information_schema.columns
            where table_schema in ('epidemic', 'epidemic_kg')
              and table_name = any(%s)
            order by table_schema, table_name, ordinal_position
            """,
            (list(wanted),),
        )
        columns: dict[str, list[dict[str, str]]] = {}
        for schema, table, column, dtype in cur.fetchall():
            columns.setdefault(f"{schema}.{table}", []).append({"name": column, "type": dtype})
        result["columns"] = columns

        cur.execute(
            """
            select status, count(*)
            from epidemic_kg.kg_qwen_heldout_registry
            group by status
            order by status
            """
        )
        result["heldout_status_counts"] = {status: count for status, count in cur.fetchall()}

        cur.execute(
            """
            select track_key, identity, status
            from epidemic_kg.kg_qwen_heldout_registry
            order by registered_at
            """
        )
        result["heldout_tracks"] = [
            {"track_key": track_key, "identity": identity, "status": status}
            for track_key, identity, status in cur.fetchall()
        ]
        conn.rollback()

print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
