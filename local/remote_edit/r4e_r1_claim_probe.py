from pathlib import Path

import psycopg

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
rows = conn.execute(
    """
    SELECT table_schema, table_name, string_agg(column_name, ',' ORDER BY ordinal_position) AS cols
    FROM information_schema.columns
    WHERE table_schema IN ('epidemic','epidemic_p1_12')
    GROUP BY table_schema, table_name
    HAVING bool_or(
        column_name IN (
            'claim_id',
            'evidence_eligible',
            'recommendation_eligible',
            'execution_ready_after_human_approval',
            'promotion_allowed'
        )
    )
    ORDER BY table_schema, table_name
    """
).fetchall()
for row in rows:
    print(row)
conn.close()
