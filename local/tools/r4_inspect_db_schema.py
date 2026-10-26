#!/usr/bin/env python3
import json, sys
from pathlib import Path
sys.path.insert(0, "USER_HOME/projects/openclaw/runtime/python-packages")
import psycopg  # type: ignore

c = psycopg.connect(host="USER_HOME/projects/openclaw/runtime/postgres-run", port=55432, user="postgres", dbname="epidemic_openclaw")
with c, c.cursor() as cur:
    cur.execute("""
    select table_schema, table_name from information_schema.tables
    where table_schema in ('epidemic','epidemic_kg','epidemic_forecast')
    order by table_schema, table_name
    """)
    tables = [f"{a}.{b}" for a, b in cur.fetchall()]
    cols = {}
    for t in tables:
        s, n = t.split(".", 1)
        cur.execute("""
        select column_name, data_type, is_nullable
        from information_schema.columns
        where table_schema=%s and table_name=%s
        order by ordinal_position
        """, (s, n))
        cols[t] = cur.fetchall()
print(json.dumps({"tables": tables, "columns": cols}, ensure_ascii=False, indent=2))
