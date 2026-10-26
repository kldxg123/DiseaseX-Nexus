#!/usr/bin/env python3
"""Protected-state fingerprint: row counts + content hashes for epidemic_kg and trusted observation tables."""
import json, os, subprocess, sys, hashlib, datetime

ROOT = "USER_HOME/projects/openclaw"
PSQL = os.path.join(ROOT, "runtime/postgres18x/bin/psql")
SOCK = os.path.join(ROOT, "runtime/postgres-run")
OUT = sys.argv[1]

def psql(sql):
    p = subprocess.run([PSQL, "-h", SOCK, "-p", "55432", "-U", "postgres",
                        "-d", "epidemic_openclaw", "-tAX", "-c", sql],
                       capture_output=True, text=True, timeout=600)
    return p.stdout.strip(), p.stderr.strip()

def table_hash(schema, table):
    # deterministic content hash: md5 over md5 row hashes ordered
    sql = (f"select coalesce(md5(string_agg(t.h, '' order by t.h)), 'empty') from "
           f"(select md5(row_to_json(r)::text) as h from {schema}.{table} r) t")
    out, err = psql(sql)
    return out if not err else f"ERROR:{err[:200]}"

fp = {"captured_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(), "tables": {}}

# all epidemic_kg tables
tables, _ = psql("select table_name from information_schema.tables where table_schema='epidemic_kg' order by 1")
for t in tables.splitlines():
    cnt, _ = psql(f"select count(*) from epidemic_kg.{t}")
    fp["tables"][f"epidemic_kg.{t}"] = {"rows": cnt, "md5": table_hash("epidemic_kg", t)}

# trusted observation tables in epidemic schema (candidate names)
cand, _ = psql("""select table_name from information_schema.tables
where table_schema='epidemic' and (
  table_name ~ 'observation|trusted|case|series|measurement|report'
) order by 1""")
for t in cand.splitlines():
    cnt, _ = psql(f"select count(*) from epidemic.{t}")
    fp["tables"][f"epidemic.{t}"] = {"rows": cnt, "md5": table_hash("epidemic", t)}

# parent run fingerprint
run, err = psql("""select row_to_json(r)::text from epidemic_kg.kg_preexecution_scoring_runs r
where run_id='9cdc3d93-3155-523e-887f-cd6068196b08'""")
fp["parent_run"] = {
    "run_id": "9cdc3d93-3155-523e-887f-cd6068196b08",
    "found": bool(run),
    "row_md5": hashlib.md5(run.encode()).hexdigest() if run else None,
    "row": json.loads(run) if run else None,
}
# candidates of parent run
cands, _ = psql("""select count(*) from epidemic_kg.kg_preexecution_model_candidates
where scoring_run_id='9cdc3d93-3155-523e-887f-cd6068196b08'""" ) if True else ("?", "")
if "ERROR" not in cands:
    fp["parent_run"]["candidate_count"] = cands

with open(OUT, "w") as f:
    json.dump(fp, f, indent=2, ensure_ascii=False)
print("fingerprint written:", OUT)
