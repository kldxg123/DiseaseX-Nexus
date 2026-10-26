#!/usr/bin/env python3
import csv, json, sys
from pathlib import Path
sys.path.insert(0, "USER_HOME/projects/openclaw/runtime/python-packages")
import psycopg  # type: ignore
PROJECT=Path("USER_HOME/projects/openclaw")
conn=psycopg.connect(host=str(PROJECT/"runtime/postgres-run"), port=55432, user="postgres", dbname="epidemic_openclaw")
with conn, conn.cursor() as cur:
    cur.execute("""
    select country_alpha3, disease_slug, granularity, metric_code,
           count(*) n,
           count(*) filter (where value_numeric is not null) non_null,
           count(distinct period_start) unique_periods,
           min(period_start), max(period_start),
           max(source_key)
    from epidemic.training_observations_v1
    group by country_alpha3, disease_slug, granularity, metric_code
    order by granularity, non_null desc, country_alpha3, disease_slug
    limit 200
    """)
    rows=[dict(zip(["country_alpha3","disease_slug","granularity","metric_code","n","non_null","unique_periods","start","end","source_key"], r)) for r in cur.fetchall()]
print(json.dumps(rows, ensure_ascii=False, default=str, indent=2))
