"""Verify dataset A (AUT legionella) and B (PER influenza) plus R3C identity values."""
import json

from db import connect

conn = connect()
out = {}

# 1. dashboard_history_series_v2 for A and B candidates
series = conn.execute(
    "select * from epidemic.dashboard_history_series_v2 limit 1"
)
cols = [d.name for d in series.description]
out["series_columns"] = cols

rows = conn.execute(
    "select country_alpha3, disease_slug, metric_code, granularity, count(*) n, "
    "min(period_start) first_p, max(period_start) last_p, "
    "count(*) filter (where value_numeric > 0) nonzero, "
    "count(distinct location_key) locs "
    "from epidemic.dashboard_history_series_v2 "
    "where (country_alpha3='AUT' and disease_slug='legionella') "
    "   or (country_alpha3='PER' and disease_slug='influenza') "
    "group by 1,2,3,4 order by 1,2,3"
).fetchall()
agg_cols = ["country", "disease", "metric", "granularity", "n_obs", "first_period",
            "last_period", "nonzero", "location_count"]
out["candidate_series"] = [dict(zip(agg_cols, r)) for r in rows]

# 2. kg_data_profiles / snapshots for the expected profile ids
for label, pid in [("A", "96e767d3-7dbd-56d8-949d-dc407966bfd2"),
                   ("B", "a324e51a-45a6-574e-a823-df7ac4465efa")]:
    r = conn.execute(
        "select * from epidemic_kg.kg_data_profiles where profile_id=%s", (pid,)
    ).fetchone()
    if r:
        pcols = [d.name for d in conn.execute("select * from epidemic_kg.kg_data_profiles limit 0").description]
        out[f"profile_{label}"] = dict(zip(pcols, r))
    else:
        out[f"profile_{label}"] = None

# 3. recommendations
for label, rid in [("A", "c219de41-ea2e-58d9-86cd-339b03213401"),
                   ("B", "ca2f64b8-2926-5ca1-9d9e-4585980849c2")]:
    r = conn.execute(
        "select * from epidemic_kg.kg_recommendations where recommendation_id=%s", (rid,)
    ).fetchone()
    rcols = [d.name for d in conn.execute("select * from epidemic_kg.kg_recommendations limit 0").description]
    out[f"recommendation_{label}"] = dict(zip(rcols, r)) if r else None

# 4. execution contracts
for label, cid in [("A", "c24475b7-84ac-51ea-8f83-35542a1e789a"),
                   ("B", "49915645-4ddf-53fd-b2c5-4dff56faac5e")]:
    r = conn.execute(
        "select * from epidemic_kg.kg_execution_contracts where contract_id=%s", (cid,)
    ).fetchone()
    ccols = [d.name for d in conn.execute("select * from epidemic_kg.kg_execution_contracts limit 0").description]
    out[f"contract_{label}"] = dict(zip(ccols, r)) if r else None

# 5. graph versions
gcols = [d.name for d in conn.execute("select * from epidemic_kg.kg_graph_versions limit 0").description]
grows = conn.execute("select * from epidemic_kg.kg_graph_versions order by 1").fetchall()
out["graph_versions"] = [dict(zip(gcols, r)) for r in grows]

# 6. model adapters registry
acols = [d.name for d in conn.execute("select * from epidemic_kg.kg_model_adapters limit 0").description]
arows = conn.execute("select * from epidemic_kg.kg_model_adapters order by adapter_id").fetchall()
out["model_adapters"] = [dict(zip(acols, r)) for r in arows]

# 7. existing shadow runs for A/B recommendations
scols = [d.name for d in conn.execute("select * from epidemic_kg.kg_forecast_shadow_runs limit 0").description]
srows = conn.execute("select * from epidemic_kg.kg_forecast_shadow_runs order by 1").fetchall()
out["existing_shadow_runs_AB"] = [dict(zip(scols, r)) for r in srows]
out["shadow_run_columns"] = scols

with open("verify_datasets.json", "w") as f:
    json.dump(out, f, indent=2, default=str)

print("columns:", cols)
print("series col count:", len(cols))
print("candidate series:", len(out["candidate_series"]))
for s in out["candidate_series"]:
    print(json.dumps(s, default=str)[:400])
print("profile_A found:", out["profile_A"] is not None)
print("profile_B found:", out["profile_B"] is not None)
print("rec_A:", json.dumps(out["recommendation_A"], default=str)[:600] if out["recommendation_A"] else None)
print("rec_B:", json.dumps(out["recommendation_B"], default=str)[:600] if out["recommendation_B"] else None)
print("contract_A:", json.dumps(out["contract_A"], default=str)[:500] if out["contract_A"] else None)
print("contract_B:", json.dumps(out["contract_B"], default=str)[:500] if out["contract_B"] else None)
print("graph_versions:", [(g.get("graph_version_id"), g.get("status")) for g in out["graph_versions"]])
print("adapters:", [(a.get("adapter_id"), a.get("adapter_version")) for a in out["model_adapters"]])
print("existing shadow runs A/B:", len(out["existing_shadow_runs_AB"]))
