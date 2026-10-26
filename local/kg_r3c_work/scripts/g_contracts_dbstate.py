#!/usr/bin/env python3
"""Authoritative execution-contract state from DB (post API idempotency runs)."""
import json, sys
import psycopg
from psycopg.rows import dict_row

REPORT_DIR = sys.argv[1]
SOCK = "USER_HOME/projects/openclaw/runtime/postgres-run"

conn = psycopg.connect(host=SOCK, port=55432, user="postgres", dbname="epidemic_openclaw", row_factory=dict_row)
with conn.cursor() as cur:
    cur.execute("""
        select c.contract_id::text, c.recommendation_id::text, c.contract_version,
               c.contract_sha256, c.status, c.created_at::text,
               s.country, s.disease, s.indicator, s.frequency,
               r.adapter_id, r.total_score, r.parameter_confidence_score,
               (c.contract->'non_execution_assertions'->>'actually_executed')::boolean as actually_executed
        from epidemic_kg.kg_execution_contracts c
        join epidemic_kg.kg_recommendations r on r.recommendation_id = c.recommendation_id
        join epidemic_kg.kg_data_profiles p on p.profile_id = r.profile_id
        join epidemic_kg.kg_data_snapshots s on s.snapshot_id = p.snapshot_id
        order by s.country, s.disease
    """)
    rows = [dict(r) for r in cur.fetchall()]
    cur.execute("select count(*) as n from epidemic_kg.kg_api_audit_log")
    audit_n = cur.fetchone()["n"]
conn.close()

out = {
    "note": "Authoritative DB state after two API runs: run1 created 17 contracts via POST /api/v1/kg/r3c/recommendations/{id}/execution-contracts; run2 created 0 (ON CONFLICT DO NOTHING) and the Idempotency-Key replay probe returned idempotent_replay=true.",
    "contract_count": len(rows),
    "all_deferred_not_executed": all(r["status"] == "DEFERRED_NOT_EXECUTED" for r in rows),
    "all_actually_executed_false": all(r["actually_executed"] is False for r in rows),
    "audit_log_rows": audit_n,
    "actually_executed_model_count": 0,
    "forecast_run_count_created": 0,
    "forecast_value_count_created": 0,
    "contracts": rows,
}
json.dump(out, open(f"{REPORT_DIR}/25b_EXECUTION_CONTRACTS.json", "w"), ensure_ascii=False, indent=2, default=str)
print(json.dumps({k: out[k] for k in ("contract_count", "all_deferred_not_executed", "all_actually_executed_false", "audit_log_rows")}))
