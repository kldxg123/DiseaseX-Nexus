#!/usr/bin/env python3
"""Read-only selection of the live Brazil+dengue R3C recommendation object."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

import psycopg


DSN = (
    "host=USER_HOME/projects/openclaw/runtime/postgres-run "
    "port=55432 dbname=epidemic_openclaw user=postgres"
)


def plain(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    return value


def dicts(cur: psycopg.Cursor[Any]) -> list[dict[str, Any]]:
    names = [d.name for d in cur.description]
    return [{names[i]: plain(v) for i, v in enumerate(row)} for row in cur.fetchall()]


def main() -> None:
    out: dict[str, Any] = {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "transaction": "REPEATABLE_READ_READ_ONLY",
        "writes": 0,
    }
    with psycopg.connect(DSN) as conn:
        conn.execute("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        conn.execute("SET LOCAL statement_timeout = '30s'")
        out["transaction_read_only"] = conn.execute("SHOW transaction_read_only").fetchone()[0]
        profiles = dicts(
            conn.execute(
                """
                SELECT p.profile_id, p.snapshot_id, p.profile_sha256, p.profiler_version,
                       s.country, s.disease, s.indicator, s.frequency, s.source_table,
                       s.n_obs, s.first_date, s.last_date, s.series_sha256, s.snapshot_sha256,
                       p.profile_json
                FROM epidemic_kg.kg_data_profiles p
                JOIN epidemic_kg.kg_data_snapshots s ON s.snapshot_id = p.snapshot_id
                WHERE upper(s.country) = 'BRA' AND lower(s.disease) = 'dengue'
                ORDER BY (s.indicator = 'cases_new') DESC,
                         (s.frequency = 'week') DESC,
                         s.n_obs DESC, p.profile_id
                """
            )
        )
        out["profiles"] = profiles
        selected = profiles[0] if profiles else None
        out["selected_profile"] = selected
        if selected:
            pid = selected["profile_id"]
            out["recommendations"] = dicts(
                conn.execute(
                    """
                    SELECT r.recommendation_id, r.run_id, r.profile_id, r.adapter_id,
                           r.bundle_id, r.is_baseline, r.total_score,
                           r.parameter_confidence_score, r.recomputed_total,
                           r.recomputed_param, r.rank, r.eligibility_status,
                           r.ineligibility_reasons, r.gap_list,
                           ec.contract_id, ec.contract_version, ec.status AS contract_status,
                           ec.contract_sha256
                    FROM epidemic_kg.kg_recommendations r
                    LEFT JOIN epidemic_kg.kg_execution_contracts ec
                      ON ec.recommendation_id = r.recommendation_id
                    WHERE r.profile_id = %s
                    ORDER BY (r.eligibility_status = 'ELIGIBLE') DESC,
                             r.is_baseline, r.rank, r.recommendation_id
                    """,
                    (pid,),
                )
            )
        else:
            out["recommendations"] = []
        out["latest_published_graph_version"] = dicts(
            conn.execute(
                """
                SELECT graph_version_id, import_run_id, parent_graph_version_id,
                       document_set_hash, assertion_set_hash, status, published_at
                FROM epidemic_kg.kg_graph_versions
                WHERE status = 'PUBLISHED'
                ORDER BY published_at DESC, graph_version_id
                LIMIT 1
                """
            )
        )
        out["recommendation_runs"] = dicts(
            conn.execute(
                """
                SELECT run_id, graph_version_id, policy_id, profile_set_hash,
                       score_type, status, actually_executed, forecast_run_id,
                       execution_status, created_at
                FROM epidemic_kg.kg_recommendation_runs
                ORDER BY created_at DESC, run_id
                """
            )
        )
        conn.execute("ROLLBACK")
    canonical = json.dumps(out, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    out["object_set_sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
