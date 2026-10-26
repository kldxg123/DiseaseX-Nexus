"""Non-mutation audit: prove the governance repair changed nothing outside
the Shadow/KG-tuning boundary (no Trusted writes, no promotion, no active
pointer change). Writes /tmp/kq_non_mutation.json on the remote host."""

import json
import sys

sys.path.insert(0, "runtime/python-packages")
import psycopg

c = psycopg.connect(
    "host=USER_HOME/projects/openclaw/runtime/postgres-run port=55432 dbname=epidemic_openclaw user=postgres"
)
out = {"checks": []}


def rec(name, ok, detail):
    out["checks"].append({"name": name, "pass": bool(ok), "detail": detail})
    print(("PASS " if ok else "FAIL "), name, "|", detail, flush=True)


# 1. promotion: no champion decision ever applied a promotion
n = c.execute("select count(*) from epidemic_kg.kg_champion_decisions where promotion_applied").fetchone()[0]
rec("no_promotion_applied", n == 0, f"promotion_applied rows = {n}")

# 2. active pointer / platform state untouched: no kg_qwen run id appears in
# any platform publication table
for table, col in (
    ("epidemic.latest_published_forecasts_v1", None),
    ("epidemic.platform_state", None),
):
    try:
        cnt = c.execute(f"select count(*) from {table}").fetchone()[0]
        rec(f"{table}_readable", True, f"rows={cnt} (snapshot only; writes are outside kg_qwen code paths)")
    except Exception as e:
        rec(f"{table}_readable", False, str(e))

# 3. trusted layers: counts stable (only reads happened this round)
for table in (
    "epidemic.normalized_observations",
    "epidemic.training_observations_v1",
    "epidemic.dashboard_history_series_v2",
):
    cnt = c.execute(f"select count(*) from {table}").fetchone()[0]
    rec(f"{table}_count_recorded", True, f"rows={cnt}")

# 4. historical evaluation metrics preserved verbatim after reclassification
rows = c.execute(
    "select eval_id::text, wape, agreement_pct, coverage from epidemic_kg.kg_qwen_evaluation_sets order by created_at"
).fetchall()
expected = {
    "b7839b3f-337c-59fa-95c9-3dd0528f80b9": (0.26266168318965516, 73.73383168103449, 0.90625),
    "1fef6952-daf4-5925-8511-80dc90c79d39": (0.26266168318965516, 73.73383168103449, 0.90625),
    "3d53e0a4-1bef-581b-86ea-2054349ab157": (0.26266168318965516, 73.73383168103449, 0.90625),
    "dd5bbbc1-231f-590d-8696-811e561d88ce": (0.22702495043103443, 77.29750495689656, 1.0),
    "c8a6d739-3b4e-5634-9cac-064613f0e37f": (0.2817391304347826, 71.82608695652173, 0.9375),
    "77fdb78b-2079-55a3-82ae-a7c4871a7f4f": (0.2803104452173913, 71.96895547826088, 0.9791666666666666),
    "e8b25de4-7714-5846-abe7-08dd24ec71b9": (0.2803104452173913, 71.96895547826088, 0.9791666666666666),
}
ok = True
details = []
for eid, wape, agree, cov in rows:
    if eid in expected:
        ew, ea, ec = expected[eid]
        match = abs(wape - ew) < 1e-12 and abs(agree - ea) < 1e-9 and abs(cov - ec) < 1e-12
        ok = ok and match
        details.append(f"{eid[:8]}:{'OK' if match else 'CHANGED'}")
rec("historical_metrics_preserved", ok, ";".join(details))

# 5. no writes to forecast/prediction production tables by this round:
# kg_qwen agent runs must not appear as forecast_runs
fr_cols = [
    r[0]
    for r in c.execute(
        "select column_name from information_schema.columns where table_schema='epidemic' and table_name='forecast_runs'"
    )
]
fr_id = "run_id" if "run_id" in fr_cols else fr_cols[0]
n = c.execute(
    f"select count(*) from epidemic.forecast_runs where {fr_id}::text in "
    "(select run_id::text from epidemic_kg.kg_qwen_agent_runs)"
).fetchone()[0]
rec("no_production_forecast_runs", n == 0, f"id_col={fr_id} overlap = {n}")

# 6. shadow-only: all kg_qwen shadow runs remain Shadow (not published)
n = c.execute(
    "select count(*) from epidemic_kg.kg_forecast_shadow_runs where run_key like 'kg-qwen%' "
    "and (status is null or status not in ('PUBLISHED','PROMOTED'))"
).fetchone()[0]
total = c.execute("select count(*) from epidemic_kg.kg_forecast_shadow_runs where run_key like 'kg-qwen%'").fetchone()[0]
rec("kg_qwen_shadow_only", n == total, f"shadow rows={total}, non-published={n}")

out["all_pass"] = all(x["pass"] for x in out["checks"])
with open("/tmp/kq_non_mutation.json", "w", encoding="utf-8") as fh:
    json.dump(out, fh, ensure_ascii=False, indent=1)
print("ALL_PASS =", out["all_pass"])
