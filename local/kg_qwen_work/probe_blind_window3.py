import json
import sys

sys.path.insert(0, "runtime/python-packages")
import psycopg

c = psycopg.connect(
    "host=USER_HOME/projects/openclaw/runtime/postgres-run port=55432 dbname=epidemic_openclaw user=postgres"
)
out = {}
out["normalized_series"] = [
    [str(x) for x in r]
    for r in c.execute(
        """
        select country_alpha2, disease_id::text, metric_code, granularity, count(*) n,
               min(period_start)::text f, max(period_start)::text l
        from epidemic.normalized_observations
        group by 1,2,3,4 having count(*) >= 100
        order by n desc limit 30
        """
    )
]
out["reference_datasets"] = [
    [str(x) for x in r]
    for r in c.execute("select * from epidemic.reference_datasets limit 10")
]
out["reference_cols"] = [
    r[0]
    for r in c.execute(
        "select column_name from information_schema.columns where table_schema='epidemic' and table_name='reference_datasets'"
    )
]
# every country in the 5 big platform dataset snapshots (are ALL legionella countries opened?)
out["platform_snapshot_countries"] = [
    [str(x) for x in r]
    for r in c.execute(
        """
        select o.country_alpha3, count(distinct d.snapshot_id) snaps, max(o.period_start)::text
        from epidemic.dataset_snapshot_observations d
        join epidemic.training_observations_v1 o on o.observation_id = d.observation_id
        where o.disease_slug = 'legionella'
        group by 1 order by 1
        """
    )
]
print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
