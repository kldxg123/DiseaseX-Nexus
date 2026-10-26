#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import psycopg


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--report-dir", required=True)
    args = p.parse_args()
    with psycopg.connect(
        dbname="postgres",
        user="postgres",
        host="USER_HOME/projects/openclaw/runtime/postgres-run",
        port=55432,
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select datname from pg_database where datname like 'epidemic_kg_prediction_r4r1%' order by 1"
            )
            dbs = [r[0] for r in cur.fetchall()]
    print(json.dumps({"databases": dbs, "report_dir_exists": Path(args.report_dir).is_dir()}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
