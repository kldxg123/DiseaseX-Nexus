import json
import sys

sys.path.insert(0, "runtime/python-packages")
import psycopg

c = psycopg.connect(
    "host=USER_HOME/projects/openclaw/runtime/postgres-run port=55432 dbname=epidemic_openclaw user=postgres"
)
out = {}

# What did the main platform do with DEU? run metadata + training data range
out["prediction_results_deu"] = [
    [str(x) for x in r]
    for r in c.execute(
        """
        select run_id::text, model_key, scope, series_key, target_week::text,
               feature_cutoff::text, created_at::text from epidemic.prediction_results
        where country_iso3 = 'DEU' order by created_at limit 10
        """
    )
]
# forecast values coverage on DEU (which target periods were forecast => trained on earlier data)
out["forecast_values_deu_span"] = [
    [str(x) for x in r]
    for r in c.execute(
        """
        select min(target_week)::text, max(target_week)::text, max(data_through_week)::text, count(*)
        from epidemic.forecast_values where country_iso3 = 'DEU'
        """
    )
]
# dataset snapshots containing DEU rows: which snapshots, what span
out["deu_dataset_snapshot_span"] = [
    [str(x) for x in r]
    for r in c.execute(
        """
        select d.snapshot_id::text, count(*), min(o.period_start)::text, max(o.period_start)::text
        from epidemic.dataset_snapshot_observations d
        join epidemic.training_observations_v1 o on o.observation_id = d.observation_id
        where o.country_alpha3 = 'DEU' and o.disease_slug = 'legionella'
        group by 1 order by 4 desc limit 10
        """
    )
]
# BRA dengue: main platform usage? candidate for blind window if unused
for ctry, dis in (("BRA", "dengue"), ("BGD", "nipah")):
    out[f"{ctry}_{dis}_snapshots"] = c.execute(
        """
        select count(*) from epidemic.dataset_snapshot_observations d
        join epidemic.training_observations_v1 o on o.observation_id = d.observation_id
        where o.country_alpha3 = %s and o.disease_slug = %s
        """,
        (ctry, dis),
    ).fetchone()[0]
    out[f"{ctry}_{dis}_forecast_values"] = c.execute(
        "select count(*) from epidemic.forecast_values where country_iso3 = %s", (ctry,)
    ).fetchone()[0]
    out[f"{ctry}_{dis}_series_len"] = [
        [str(x) for x in r]
        for r in c.execute(
            """
            select metric_code, granularity, count(*), min(period_start)::text, max(period_start)::text
            from epidemic.training_observations_v1
            where country_alpha3 = %s and disease_slug = %s
            group by 1,2
            """,
            (ctry, dis),
        )
    ]

print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
