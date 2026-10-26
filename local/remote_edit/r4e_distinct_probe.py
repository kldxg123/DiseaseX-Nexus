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
    queries = {
        "raw_artifacts.storage_mode": "select distinct storage_mode from epidemic.raw_artifacts order by 1",
        "retrieval_runs.status": "select distinct status from epidemic.retrieval_runs order by 1",
        "data_sources.source_type": "select distinct source_type from epidemic.data_sources order by 1",
        "data_sources.authority": "select distinct authority from epidemic.data_sources order by 1",
        "query_requests.source_preference": "select distinct source_preference from epidemic.query_requests order by 1",
        "query_requests.time_window_kind": "select distinct time_window_kind from epidemic.query_requests order by 1",
    }
    out = {}
    with psycopg.connect(dsn()) as conn:
        conn.execute("BEGIN")
        conn.execute("SET TRANSACTION READ ONLY")
        for k, q in queries.items():
            out[k] = [r[0] for r in conn.execute(q).fetchall()]
        conn.execute("ROLLBACK")
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
