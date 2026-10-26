from __future__ import annotations

import json
from pathlib import Path

import psycopg


def dsn() -> str:
    env = {}
    for line in Path("USER_HOME/projects/openclaw/runtime/secrets/vm201_runtime.env").read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            env[k] = v.strip().strip("'").strip('"')
    return f"postgresql://postgres:{env['PG_SUPERPASS']}@127.0.0.1:55432/epidemic_openclaw"


def main() -> None:
    names = [
        "epidemic.data_sources",
        "epidemic.retrieval_runs",
        "epidemic.raw_artifacts",
        "epidemic.query_requests",
        "epidemic.source_fetches",
        "epidemic.dataset_snapshots",
        "epidemic_p1_12.trusted_fetch_artifacts",
    ]
    out = {}
    with psycopg.connect(dsn()) as conn:
        conn.execute("BEGIN")
        conn.execute("SET TRANSACTION READ ONLY")
        for name in names:
            reg = conn.execute("select to_regclass(%s)", (name,)).fetchone()[0]
            item = {"to_regclass": str(reg) if reg else None}
            if reg:
                schema, table = name.split(".")
                cols = conn.execute(
                    """
                    select column_name, data_type, is_nullable
                    from information_schema.columns
                    where table_schema=%s and table_name=%s
                    order by ordinal_position
                    """,
                    (schema, table),
                ).fetchall()
                item["columns"] = [{"name": c[0], "type": c[1], "nullable": c[2]} for c in cols]
                item["row_count"] = conn.execute(f'select count(*) from "{schema}"."{table}"').fetchone()[0]
                cons = conn.execute(
                    "select conname, contype, pg_get_constraintdef(oid) from pg_constraint where conrelid=%s::regclass order by conname",
                    (name,),
                ).fetchall()
                item["constraints"] = cons
            out[name] = item
        conn.execute("ROLLBACK")
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
