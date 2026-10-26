import json

from epidemic_core.kg_r3c.dbutil import connect


with connect() as conn:
    with conn.cursor() as cur:
        cur.execute("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        cur.execute("SET LOCAL statement_timeout = '30s'")
        cur.execute("SET LOCAL idle_in_transaction_session_timeout = '60s'")
        cur.execute(
            """
            select column_name, data_type
            from information_schema.columns
            where table_schema = 'epidemic_p1_12'
              and table_name = 'trusted_dashboard_series_v1'
            order by ordinal_position
            """
        )
        columns = [{"column": row[0], "type": row[1]} for row in cur.fetchall()]
        cur.execute("show transaction_read_only")
        read_only = cur.fetchone()[0]
        conn.rollback()

print(json.dumps({"transaction_read_only": read_only, "columns": columns}, ensure_ascii=False, indent=2))
