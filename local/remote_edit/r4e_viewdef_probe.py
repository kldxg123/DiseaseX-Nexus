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
    views = [
        ("epidemic_p1_12", "trusted_dashboard_catalog_v1"),
        ("epidemic_p1_12", "trusted_dashboard_series_v1"),
    ]
    out = {}
    with psycopg.connect(db_url()) as conn:
        conn.execute("BEGIN")
        conn.execute("SET TRANSACTION READ ONLY")
        for schema, name in views:
            definition = conn.execute("select pg_get_viewdef(%s::regclass, true)", (f"{schema}.{name}",)).fetchone()[0]
            out[f"{schema}.{name}"] = definition
        deps = conn.execute(
            """
            select distinct source_ns.nspname as source_schema, source_cls.relname as source_table
            from pg_rewrite
            join pg_class view_cls on pg_rewrite.ev_class = view_cls.oid
            join pg_namespace view_ns on view_cls.relnamespace = view_ns.oid
            join pg_depend on pg_depend.objid = pg_rewrite.oid
            join pg_class source_cls on pg_depend.refobjid = source_cls.oid
            join pg_namespace source_ns on source_cls.relnamespace = source_ns.oid
            where view_ns.nspname = 'epidemic_p1_12'
              and view_cls.relname in ('trusted_dashboard_catalog_v1','trusted_dashboard_series_v1')
              and source_cls.relkind in ('r','p','v','m')
            order by 1,2
            """
        ).fetchall()
        out["dependencies"] = [".".join(r) for r in deps]
        for schema, table in deps:
            cols = conn.execute(
                """
                select column_name, data_type, is_nullable
                from information_schema.columns
                where table_schema=%s and table_name=%s
                order by ordinal_position
                """,
                (schema, table),
            ).fetchall()
            count = conn.execute(f'select count(*) from "{schema}"."{table}"').fetchone()[0]
            out[f"columns:{schema}.{table}"] = {
                "row_count": count,
                "columns": [{"name": c[0], "type": c[1], "nullable": c[2]} for c in cols],
            }
        conn.execute("ROLLBACK")
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
