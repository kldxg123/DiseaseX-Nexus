import json
import sys

sys.path.insert(0, "runtime/python-packages")
import psycopg

c = psycopg.connect(
    "host=USER_HOME/projects/openclaw/runtime/postgres-run port=55432 dbname=epidemic_openclaw user=postgres"
)
out = {}

out["series_ending_2025plus"] = [
    [str(x) for x in r]
    for r in c.execute(
        """
        select country_alpha3, disease_slug, metric_code, granularity, count(*) n, max(period_start)::text l
        from epidemic.training_observations_v1
        group by 1,2,3,4 having max(period_start) >= date '2025-01-01'
        order by n desc limit 20
        """
    )
]

# has the main platform ever trained on DEU legionella rows?
out["deu_in_training_snapshots"] = c.execute(
    """
    select count(*) from epidemic.training_snapshot_observations_v1 t
    join epidemic.training_observations_v1 o on o.observation_id = t.observation_id
    where o.country_alpha3 = 'DEU'
    """
).fetchone()[0]
out["deu_in_dataset_snapshots"] = c.execute(
    """
    select count(*) from epidemic.dataset_snapshot_observations d
    join epidemic.training_observations_v1 o on o.observation_id = d.observation_id
    where o.country_alpha3 = 'DEU'
    """
).fetchone()[0]

# any forecast/prediction artifacts on DEU legionella?
for t in ("forecast_runs", "prediction_runs", "forecast_values", "prediction_results"):
    cols = [
        r[0]
        for r in c.execute(
            "select column_name from information_schema.columns where table_schema='epidemic' and table_name=%s",
            (t,),
        )
    ]
    out[f"{t}_cols"] = [x for x in cols if any(k in x for k in ("country", "disease", "metric", "series"))]

# prior forecast tasks/shadow runs in kg schema touching DEU?
out["deu_kg_forecast_tasks"] = c.execute(
    "select count(*) from epidemic_kg.kg_forecast_tasks where track_key like 'DEU%'"
).fetchone()[0] if c.execute(
    "select count(*) from information_schema.columns where table_schema='epidemic_kg' and table_name='kg_forecast_tasks' and column_name='track_key'"
).fetchone()[0] else "no track_key col"

# distinct countries ever seen in kg shadow runs / backtests (what the KG phases touched)
out["kg_shadow_run_keys"] = [
    r[0]
    for r in c.execute(
        "select distinct run_key from epidemic_kg.kg_forecast_shadow_runs order by 1 limit 30"
    )
] if c.execute(
    "select count(*) from information_schema.columns where table_schema='epidemic_kg' and table_name='kg_forecast_shadow_runs' and column_name='run_key'"
).fetchone()[0] else "n/a"

print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
