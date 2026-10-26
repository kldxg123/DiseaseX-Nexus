from __future__ import annotations
import json
import sys
from epidemic_core.kg_qwen.pipeline import TRACK_G_KEY, execute_autotuning
from epidemic_core.kg_r3c.dashboard_app import write_conn

IDEMPOTENCY_KEY = "kg-qwen-zaf-who-fluid-sari-cases-fresh-20260814-v1"

def scalar(conn, sql: str) -> int:
    row = conn.execute(sql).fetchone()
    value = next(iter(row.values())) if hasattr(row, "values") else row[0]
    return int(value)

def protected_counts(conn) -> dict[str, int]:
    return {
        "trusted_observations": scalar(conn, "select count(*) from epidemic.normalized_observations"),
        "forecast_active_runs": scalar(conn, "select count(*) from epidemic.forecast_runs where status in ('PUBLISHED','ACTIVE')"),
        "promotion_events": scalar(conn, "select count(*) from epidemic_kg.kg_api_audit_log where endpoint ilike '%promotion%'"),
    }

def main() -> None:
    with write_conn() as conn:
        before = protected_counts(conn)
        result = execute_autotuning(
            conn,
            actor="codex_isolated_fresh_evaluation",
            idempotency_key=IDEMPOTENCY_KEY,
            track_key=TRACK_G_KEY,
        )
        conn.commit()
        after = protected_counts(conn)
    safe = {
        "result": result,
        "protected_counts_before": before,
        "protected_counts_after": after,
        "protected_count_delta": {key: after[key] - before[key] for key in before},
    }
    print(json.dumps(safe, ensure_ascii=False, sort_keys=True, default=str))
    if any(safe["protected_count_delta"].values()):
        raise SystemExit("PROTECTED_COUNT_CHANGED")

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"status": "FRESH_RUN_FAILED", "error_type": type(exc).__name__, "error": str(exc)[:500]}, sort_keys=True), file=sys.stderr)
        raise
