from pathlib import Path

import psycopg

TARGETS = (
    "7b8ea8a8-2f4f-5975-b3ad-08f6a03ccaf4",
    "6ef6f7e4-f510-5358-8c84-50ecd73283de",
)
env: dict[str, str] = {}
for line in Path("USER_HOME/projects/openclaw/runtime/secrets/vm201_runtime.env").read_text().splitlines():
    if "=" in line and not line.strip().startswith("#"):
        key, value = line.split("=", 1)
        env[key] = value

conn = psycopg.connect(
    host="127.0.0.1",
    port=55432,
    dbname="epidemic_openclaw",
    user="postgres",
    password=env["PG_SUPERPASS"],
)
tables = conn.execute(
    """
    SELECT table_schema, table_name
    FROM information_schema.tables
    WHERE table_schema IN ('epidemic','epidemic_p1_12')
      AND table_type='BASE TABLE'
    ORDER BY table_schema, table_name
    """
).fetchall()
for schema, table in tables:
    cols = conn.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema=%s AND table_name=%s
          AND data_type IN ('uuid','text','character varying','jsonb','json')
        ORDER BY ordinal_position
        """,
        (schema, table),
    ).fetchall()
    if not cols:
        continue
    predicates = [f"{col[0]}::text LIKE %s" for col in cols]
    sql = f'SELECT COUNT(*) FROM "{schema}"."{table}" WHERE ' + " OR ".join(predicates)
    for target in TARGETS:
        args = tuple(f"%{target}%" for _ in cols)
        count = conn.execute(sql, args).fetchone()[0]
        if count:
            print(schema + "." + table, target, count)
conn.close()
