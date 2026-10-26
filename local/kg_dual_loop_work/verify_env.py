"""Environment verification for KG_DUAL_DATASET_RECOMMENDATION_FORECAST_FEEDBACK_LOOP."""
import json

from db import connect

conn = connect()
out = {}

rows = conn.execute(
    "select table_schema, table_name from information_schema.tables "
    "where table_schema in ('epidemic_kg','epidemic','epidemic_preview') "
    "and table_type='BASE TABLE' order by 1,2"
).fetchall()
out["tables"] = [f"{r[0]}.{r[1]}" for r in rows]

# business table counts
counts = {}
for t in out["tables"]:
    sch, name = t.split(".", 1)
    try:
        n = conn.execute(f'select count(*) from "{sch}"."{name}"').fetchone()[0]
        counts[t] = n
    except Exception as e:  # noqa
        counts[t] = f"ERR {e}"
out["table_counts"] = counts

with open("env_tables.json", "w") as f:
    json.dump(out, f, indent=2, default=str)
print(json.dumps(out["table_counts"], indent=2, default=str))
