from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("USER_HOME/projects/openclaw")
REPORT_DIR = Path("USER_HOME/projects/openclaw/reports/forecast_model_kg_v1_20260729T053519Z_nogit")
PSQL = ROOT / "runtime/postgres18x/bin/psql"
SOCKET = ROOT / "runtime/postgres-run"
DB = "epidemic_openclaw"

TABLES = [
    "epidemic.query_requests",
    "epidemic.source_candidates",
    "epidemic.source_review_cases",
    "epidemic.review_decisions",
    "epidemic.data_sources",
    "epidemic.observations",
    "epidemic.trusted_observations",
    "epidemic.forecast_runs",
    "epidemic.forecast_predictions",
    "epidemic.snapshots",
    "epidemic.snapshot_members",
    "epidemic.publications",
    "epidemic.schedules",
]

PROTECTED_FORECAST_IDS = [
    "c1ce8ef0-fd86-5253-940e-7f6a30b354d5",
    "2adad614-7f25-5e58-ba00-167ca5f87606",
]


def run_sql(sql: str) -> list[str]:
    cmd = [
        str(PSQL),
        "-h",
        str(SOCKET),
        "-p",
        "55432",
        "-U",
        "postgres",
        "-d",
        DB,
        "-At",
        "-v",
        "ON_ERROR_STOP=1",
        "-c",
        "BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY; "
        "SET LOCAL statement_timeout = '30s'; "
        "SET LOCAL idle_in_transaction_session_timeout = '60s'; "
        + sql
        + "; ROLLBACK;",
    ]
    out = subprocess.check_output(cmd, text=True)
    return [line for line in out.splitlines() if line and not line.startswith(("BEGIN", "SET", "ROLLBACK"))]


def scalar(sql: str) -> str | None:
    lines = run_sql(sql)
    return lines[0] if lines else None


def table_count(table: str) -> dict[str, object]:
    exists = scalar(f"SELECT to_regclass('{table}') IS NOT NULL") == "t"
    result: dict[str, object] = {"exists": exists}
    if exists:
        result["row_count"] = int(scalar(f"SELECT count(*) FROM {table}") or "0")
    return result


def has_column(table: str, column: str) -> bool:
    schema, name = table.split(".", 1)
    return (
        scalar(
            "SELECT EXISTS ("
            "SELECT 1 FROM information_schema.columns "
            f"WHERE table_schema='{schema}' AND table_name='{name}' AND column_name='{column}'"
            ")"
        )
        == "t"
    )


def main() -> None:
    payload: dict[str, object] = {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "mode": "READ_ONLY_REPEATABLE_READ",
        "database_writes": 0,
        "tables": {table: table_count(table) for table in TABLES},
    }
    if payload["tables"].get("epidemic.forecast_runs", {}).get("exists"):
        quoted = ",".join("'" + item + "'" for item in PROTECTED_FORECAST_IDS)
        reason_expr = "''"
        if has_column("epidemic.forecast_runs", "invalidation_reason"):
            reason_expr = "coalesce(invalidation_reason::text,'')"
        elif has_column("epidemic.forecast_runs", "retraction_reason"):
            reason_expr = "coalesce(retraction_reason::text,'')"
        dataset_expr = "''"
        if has_column("epidemic.forecast_runs", "dataset_sha256"):
            dataset_expr = "coalesce(dataset_sha256::text,'')"
        rows = run_sql(
            "SELECT id::text, coalesce(status::text,''), "
            f"{dataset_expr}, {reason_expr} "
            f"FROM epidemic.forecast_runs WHERE id::text IN ({quoted}) ORDER BY id::text"
        )
        payload["protected_forecast_runs"] = [
            {
                "id": parts[0],
                "status": parts[1],
                "dataset_sha256": parts[2],
                "invalid_or_retracted_reason": parts[3],
            }
            for parts in (line.split("|", 3) for line in rows)
        ]
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    output = REPORT_DIR / "PROTECTED_STATE_FINGERPRINT_CURRENT.json"
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
