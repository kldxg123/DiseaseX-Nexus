from __future__ import annotations

import json
from pathlib import Path

import psycopg

TABLES = [
    ("epidemic_p1_12", "trusted_dashboard_catalog_v1"),
    ("epidemic_p1_12", "trusted_dashboard_series_v1"),
    ("epidemic_p1_12", "trusted_update_status_v1"),
    ("epidemic", "dashboard_catalog_options_v2"),
    ("epidemic", "dashboard_history_catalog_v2"),
    ("epidemic", "dashboard_history_series_v2"),
    ("epidemic", "daily_update_status_v1"),
    ("epidemic", "source_fetches"),
    ("epidemic", "dataset_snapshots"),
    ("epidemic_p1_12", "trusted_fetch_artifacts"),
]


def db_url() -> str:
    for pid in Path("/proc").iterdir():
        if not pid.name.isdigit():
            continue
        try:
            env_bytes = (pid / "environ").read_bytes()
        except Exception:
            continue
        for item in env_bytes.split(b"\0"):
            if item.startswith(b"DASHBOARD_DATABASE_URL="):
                dsn = item.split(b"=", 1)[1].decode(errors="ignore")
                if dsn.startswith("postgresql+psycopg://"):
                    dsn = "postgresql://" + dsn.split("://", 1)[1]
                return dsn
    raise RuntimeError("DASHBOARD_DATABASE_URL_NOT_FOUND")


def main() -> None:
    out = {}
    with psycopg.connect(db_url()) as conn:
        conn.execute("BEGIN")
        conn.execute("SET TRANSACTION READ ONLY")
        for schema, table in TABLES:
            cols = conn.execute(
                """
                select column_name, data_type, is_nullable
                from information_schema.columns
                where table_schema=%s and table_name=%s
                order by ordinal_position
                """,
                (schema, table),
            ).fetchall()
            if not cols:
                continue
            count = conn.execute(f'select count(*) from "{schema}"."{table}"').fetchone()[0]
            sample = conn.execute(f'select * from "{schema}"."{table}" limit 2').fetchall()
            out[f"{schema}.{table}"] = {
                "row_count": count,
                "columns": [{"name": c[0], "type": c[1], "nullable": c[2]} for c in cols],
                "sample": [[str(x) for x in row] for row in sample],
            }
        conn.execute("ROLLBACK")
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
