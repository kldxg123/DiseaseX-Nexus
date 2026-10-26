from __future__ import annotations

import json
from pathlib import Path

import psycopg

DSN = "postgresql://REDACTED@/epidemic_openclaw?host=USER_HOME/projects/openclaw/runtime/postgres-run&port=55432"

out = {}
with psycopg.connect(DSN, autocommit=False) as conn:
    with conn.cursor() as cur:
        cur.execute("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        cur.execute("SET LOCAL statement_timeout='30s'")
        for name, sql in {
            "node_types": "select node_type, count(*) from epidemic_kg.kg_nodes group by 1 order by 2 desc",
            "edge_predicates": "select predicate, count(*) from epidemic_kg.kg_edges group by 1 order by 2 desc limit 40",
            "claim_statuses": "select review_status, claim_status, quarantine, evidence_eligible, count(*) from epidemic_kg.kg_claims group by 1,2,3,4 order by 5 desc",
            "shadow_runs": "select run_id::text, idempotency_key, mode, status, disease_slug, country_alpha3, metric_code, cadence, data_through, model_name, promotion_eligible, active_pointer_changed, trusted_rows_changed from epidemic_kg.kg_forecast_shadow_runs order by created_at desc limit 20",
            "training_series": "select country_alpha3, disease_slug, granularity, metric_code, count(*) n, count(value_numeric) nn, min(period_start), max(period_start), count(distinct source_key) source_count from epidemic.training_observations_v1 where value_numeric is not null group by 1,2,3,4 order by nn desc, max(period_start) desc limit 40",
            "forecast_protected": "select run_id::text,status,dataset_sha256,model_key,source_data_through from epidemic.prediction_runs where run_id::text in ('c1ce1947-1ab4-5f2d-b69e-a4e9c4668825','2adad614-7f25-5e58-ba00-167ca5f87606') order by run_id::text",
        }.items():
            try:
                cur.execute(sql)
                cols = [d.name for d in cur.description]
                out[name] = [dict(zip(cols, r)) for r in cur.fetchall()]
            except Exception as exc:
                out[name] = {"error_type": type(exc).__name__, "error": str(exc)[:250]}
        cur.execute("ROLLBACK")
print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
