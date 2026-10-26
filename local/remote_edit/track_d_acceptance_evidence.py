from __future__ import annotations

import json
import os
from datetime import date, datetime
from decimal import Decimal

import psycopg
from psycopg.rows import dict_row


RUN_ID = "8c6324d5-d0bb-525f-b39a-9c0500a7d8a0"
TRACK_KEY = "MYS:influenza:influenza_positive_detections:week"


def safe(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {str(key): safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [safe(item) for item in value]
    return value


def rows(conn, sql: str, params=()):
    return [safe(dict(row)) for row in conn.execute(sql, params).fetchall()]


def main() -> None:
    dsn = os.environ.get("DASHBOARD_DATABASE_URL")
    if not dsn:
        password = os.environ["EPIDEMIC_ROLE_PASS"]
        dsn = (
            "postgresql://epidemic_dashboard_ro:"
            f"{password}@127.0.0.1:55432/epidemic_openclaw"
        )
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        conn.execute("SET LOCAL statement_timeout = '30s'")
        run = rows(
            conn,
            """
            select run_id::text, track_key, status, stop_reason, target_status,
                   snapshot_sha256, split_sha256, seed, first_qwen_started_at,
                   tuning_finished_at, created_at, finished_at
            from epidemic_kg.kg_qwen_agent_runs
            where run_id = %s
            """,
            (RUN_ID,),
        )
        registry = rows(
            conn,
            """
            select registry_id::text, track_key, status, consumer_run_id::text,
                   registered_at, locked_at, first_qwen_started_at,
                   tuning_finished_at, consumed_at
            from epidemic_kg.kg_qwen_heldout_registry
            where track_key = %s
            """,
            (TRACK_KEY,),
        )
        evaluations = rows(
            conn,
            """
            select eval_id::text, run_id::text, track, identity,
                   eval_count, wape, agreement_pct, coverage, metrics
            from epidemic_kg.kg_qwen_evaluation_sets
            where run_id = %s
            """,
            (RUN_ID,),
        )
        plans = rows(
            conn,
            """
            select round_index, plan_source, validation_status, plan_json
            from epidemic_kg.kg_qwen_tuning_plans
            where run_id = %s order by round_index
            """,
            (RUN_ID,),
        )
        rounds = rows(
            conn,
            """
            select round_index, status, trials_executed, best_adapter_id,
                   best_val_wape, best_val_agreement_pct, best_val_coverage,
                   baseline_best_wape, improvement_vs_baseline, target_met,
                   stop_reason
            from epidemic_kg.kg_qwen_tuning_rounds
            where run_id = %s order by round_index
            """,
            (RUN_ID,),
        )
        shadow = rows(
            conn,
            """
            select run_id::text, mode, status, model_name,
                   promotion_eligible, active_pointer_changed,
                   trusted_rows_changed, profile, selection_trace
            from epidemic_kg.kg_forecast_shadow_runs
            where profile->>'track_key' = %s
              and profile->>'snapshot_sha256' = %s
            order by created_at desc limit 1
            """,
            (TRACK_KEY, run[0]["snapshot_sha256"]),
        )
        conn.rollback()
    payload = {
        "query_mode": "REPEATABLE_READ_READ_ONLY",
        "run": run,
        "heldout_registry": registry,
        "evaluations": evaluations,
        "qwen_plans": plans,
        "tuning_rounds": rounds,
        "shadow_runs": shadow,
        "formal_prediction_created": False,
        "trusted_or_active_pointer_changed_by_this_run": False,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
