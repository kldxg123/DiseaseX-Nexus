#!/usr/bin/env python3
import json, sys
sys.path.insert(0, "USER_HOME/projects/openclaw/runtime/python-packages")
import psycopg  # type: ignore
c=psycopg.connect(host="USER_HOME/projects/openclaw/runtime/postgres-run",port=55432,user="postgres",dbname="epidemic_openclaw")
with c, c.cursor() as cur:
    cur.execute("select conname, pg_get_constraintdef(oid) from pg_constraint where conrelid='epidemic_kg.kg_claims'::regclass order by conname")
    print(json.dumps(cur.fetchall(), ensure_ascii=False, indent=2))
