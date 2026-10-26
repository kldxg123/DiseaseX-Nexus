import json
import sys

sys.path.insert(0, "runtime/python-packages")
import psycopg

c = psycopg.connect(
    "host=USER_HOME/projects/openclaw/runtime/postgres-run port=55432 dbname=epidemic_openclaw user=postgres"
)
out = {}

# candidate: CL influenza detections (weekly, 1580 rows) and other long normalized series
cands = ["CL", "AR", "US", "PE"]
for cc in cands:
    out[f"{cc}_in_training_obs"] = c.execute(
        "select count(*) from epidemic.training_observations_v1 where country_alpha2 = %s or country_alpha3 = %s",
        (cc, cc),
    ).fetchone()[0]
    out[f"{cc}_forecast_runs"] = c.execute(
        "select count(*) from epidemic.forecast_runs where country_iso3 = %s", (cc,)
    ).fetchone()[0]
    out[f"{cc}_forecast_values"] = c.execute(
        "select count(*) from epidemic.forecast_values where country_iso3 = %s", (cc,)
    ).fetchone()[0]
    out[f"{cc}_prediction_results"] = c.execute(
        "select count(*) from epidemic.prediction_results where country_iso3 = %s", (cc,)
    ).fetchone()[0]

# kg imports / snapshots that may embed flunet data
for t, col in (("kg_data_snapshots", None), ("kg_import_runs", None), ("kg_forecast_tasks", None)):
    cols = [
        r[0]
        for r in c.execute(
            "select column_name from information_schema.columns where table_schema='epidemic_kg' and table_name=%s",
            (t,),
        )
    ]
    out[f"{t}_cols"] = cols[:14]

# any kg shadow run keys mentioning influenza/flu/CL/PER?
out["kg_shadow_keys_flu"] = [
    r[0]
    for r in c.execute(
        "select distinct run_key from epidemic_kg.kg_forecast_shadow_runs "
        "where run_key ilike '%influenza%' or run_key ilike '%flu%' or run_key ilike '%:CL:%' "
        "or run_key ilike '%:PE:%' or run_key ilike '%:AR:%' order by 1 limit 20"
    )
]
# kg pt / dual loop PER source: where did PER influenza come from?
out["per_in_training_obs"] = c.execute(
    "select count(*), min(period_start)::text, max(period_start)::text from epidemic.training_observations_v1 where country_alpha3='PER'"
).fetchone()
print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
