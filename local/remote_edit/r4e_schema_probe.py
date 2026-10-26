from __future__ import annotations

import json
import subprocess
from pathlib import Path

import psycopg

ROOT = Path("USER_HOME/projects/openclaw")


def db_url() -> str:
    for pid in Path("/proc").iterdir():
        if not pid.name.isdigit():
            continue
        try:
            env_bytes = (pid / "environ").read_bytes()
        except Exception:
            continue
        if b"DASHBOARD_DATABASE_URL=" not in env_bytes:
            continue
        for item in env_bytes.split(b"\0"):
            if item.startswith(b"DASHBOARD_DATABASE_URL="):
                dsn = item.split(b"=", 1)[1].decode(errors="ignore")
                if dsn.startswith("postgresql+psycopg://"):
                    dsn = "postgresql://" + dsn.split("://", 1)[1]
                return dsn
    raise RuntimeError("DASHBOARD_DATABASE_URL_NOT_FOUND")


def main() -> None:
    out: dict[str, object] = {}
    with psycopg.connect(db_url()) as conn:
        conn.execute("BEGIN")
        conn.execute("SET TRANSACTION READ ONLY")
        out["read_only"] = conn.execute("SHOW transaction_read_only").fetchone()[0]
        alembic_tables = conn.execute(
            """
            select table_schema, table_name
            from information_schema.tables
            where table_name='alembic_version'
            order by 1,2
            """
        ).fetchall()
        out["alembic_tables"] = [".".join(r) for r in alembic_tables]
        versions = []
        for schema, table in alembic_tables:
            versions.extend(
                {
                    "table": f"{schema}.{table}",
                    "version_num": r[0],
                }
                for r in conn.execute(f'select version_num from "{schema}"."{table}"').fetchall()
            )
        out["alembic_db"] = versions
        all_tables = conn.execute(
            """
            select table_schema, table_name
            from information_schema.tables
            where table_schema not in ('pg_catalog','information_schema')
            order by 1,2
            """
        ).fetchall()
        out["all_tables"] = [".".join(r) for r in all_tables]
        tables = conn.execute(
            """
            select table_schema, table_name from information_schema.tables
            where table_schema not in ('pg_catalog','information_schema')
            and (
              table_name ilike '%source%' or table_name ilike '%observation%' or table_name ilike '%raw%'
              or table_name ilike '%snapshot%' or table_name ilike '%contract%' or table_name ilike '%indicator%'
              or table_name ilike '%revision%'
            )
            order by 1,2
            """
        ).fetchall()
        table_info = {}
        for schema, table in tables:
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
            table_info[f"{schema}.{table}"] = {
                "row_count": count,
                "columns": [
                    {"name": c[0], "type": c[1], "nullable": c[2]}
                    for c in cols
                ],
            }
        out["tables"] = table_info
        conn.execute("ROLLBACK")
    out["alembic_heads"] = subprocess.run(
        ["python3", "-m", "alembic", "heads"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    ).stdout
    out["alembic_current"] = subprocess.run(
        ["python3", "-m", "alembic", "current"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    ).stdout
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
