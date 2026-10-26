from __future__ import annotations

import json
from pathlib import Path

import psycopg


def db_url() -> str:
    env = {}
    for line in Path("USER_HOME/projects/openclaw/runtime/secrets/vm201_runtime.env").read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            env[k] = v.strip().strip("'").strip('"')
    return f"postgresql://postgres:{env['PG_SUPERPASS']}@127.0.0.1:55432/epidemic_openclaw"


def main() -> None:
    tables = [
        "epidemic.normalized_observations",
        "epidemic_p1_12.trusted_source_contracts",
        "epidemic_p1_12.trusted_observation_lineage",
        "epidemic_p1_12.trusted_snapshot_lineage",
        "epidemic.metric_definitions",
    ]
    out = {}
    with psycopg.connect(db_url()) as conn:
        conn.execute("BEGIN")
        conn.execute("SET TRANSACTION READ ONLY")
        for table in tables:
            rows = conn.execute(
                """
                select conname, contype, pg_get_constraintdef(oid)
                from pg_constraint
                where conrelid=%s::regclass
                order by conname
                """,
                (table,),
            ).fetchall()
            indexes = conn.execute(
                """
                select indexname, indexdef from pg_indexes
                where schemaname=%s and tablename=%s
                order by indexname
                """,
                tuple(table.split(".")),
            ).fetchall()
            out[table] = {
                "constraints": rows,
                "indexes": indexes,
            }
        conn.execute("ROLLBACK")
    print(json.dumps(out, default=str, indent=2))


if __name__ == "__main__":
    main()
