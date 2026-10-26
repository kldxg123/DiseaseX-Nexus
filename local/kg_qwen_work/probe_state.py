import json
import sys

sys.path.insert(0, "runtime/python-packages")
sys.path.insert(0, "src")

import psycopg

c = psycopg.connect(
    "host=USER_HOME/projects/openclaw/runtime/postgres-run port=55432 dbname=epidemic_openclaw user=postgres"
)

out = {}

# 1. candidate trusted series for a NEW blind window: long, recent, never used
out["trusted_series"] = [
    dict(zip(("country", "disease", "indicator", "frequency", "n", "first", "last"), (str(x) for x in r)))
    for r in c.execute(
        """
        select country_iso3, disease, indicator, frequency, count(*) n,
               min(period_start)::text, max(period_start)::text
        from epidemic_kg.trusted_observations
        group by 1,2,3,4 having count(*) >= 200 order by n desc limit 40
        """
    )
]

# 2. existing snapshots (which series already have demo snapshots)
out["snapshot_cols"] = [
    r[0]
    for r in c.execute(
        "select column_name from information_schema.columns where table_schema='epidemic_kg' and table_name='kg_pt_demo_snapshots'"
    )
]
out["snapshots"] = [
    dict(zip(("demo_key", "sha", "n_rows", "created"), (str(x) for x in r)))
    for r in c.execute(
        "select demo_key, snapshot_sha256, jsonb_array_length(rows), created_at from epidemic_kg.kg_pt_demo_snapshots order by created_at"
    )
]

# 3. which tracks have agent runs (tuning already touched them)
out["agent_runs"] = [
    dict(zip(("run_id", "track_key", "status", "key", "created"), (str(x) for x in r)))
    for r in c.execute(
        "select run_id, track_key, status, idempotency_key, created_at from epidemic_kg.kg_qwen_agent_runs order by created_at"
    )
]

# 4. trusted table name sanity: list kg tables
out["tables"] = [
    r[0]
    for r in c.execute(
        "select table_name from information_schema.tables where table_schema='epidemic_kg' order by 1"
    )
]

print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
