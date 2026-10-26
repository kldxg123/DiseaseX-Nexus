#!/usr/bin/env python3
"""Strictly read-only live-state and non-mutation fingerprint probe."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

import psycopg
from psycopg import sql


DSN = (
    "host=USER_HOME/projects/openclaw/runtime/postgres-run "
    "port=55432 dbname=epidemic_openclaw user=postgres"
)

TABLES = (
    ("epidemic", "data_sources"),
    ("epidemic", "normalized_observations"),
    ("epidemic", "dataset_snapshots"),
    ("epidemic", "dataset_snapshot_observations"),
    ("epidemic", "forecast_runs"),
    ("epidemic", "forecast_values"),
    ("epidemic", "forecast_run_invalidations"),
    ("epidemic_discovery", "source_candidates"),
    ("epidemic_discovery", "source_candidate_versions"),
    ("epidemic_discovery", "source_review_cases"),
    ("epidemic_discovery", "source_review_decisions"),
    ("epidemic_discovery", "source_promotion_runs"),
    ("epidemic_discovery", "p1_9_publications"),
    ("epidemic_kg", "kg_claims"),
    ("epidemic_kg", "kg_review_decisions"),
    ("epidemic_kg", "kg_review_events"),
    ("epidemic_kg", "kg_graph_versions"),
    ("epidemic_kg", "kg_data_snapshots"),
    ("epidemic_kg", "kg_data_profiles"),
    ("epidemic_kg", "kg_preexecution_scoring_runs"),
    ("epidemic_kg", "kg_preexecution_data_profiles"),
    ("epidemic_kg", "kg_preexecution_model_candidates"),
    ("epidemic_kg", "kg_preexecution_score_components"),
    ("epidemic_kg", "kg_recommendation_runs"),
    ("epidemic_kg", "kg_recommendations"),
    ("epidemic_kg", "kg_recommendation_components"),
    ("epidemic_kg", "kg_execution_contracts"),
    ("epidemic_kg", "kg_forecast_shadow_runs"),
    ("epidemic_kg", "kg_forecast_shadow_values"),
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


def rows_as_dicts(cur: psycopg.Cursor[Any]) -> list[dict[str, Any]]:
    names = [d.name for d in cur.description]
    return [{names[i]: plain(v) for i, v in enumerate(row)} for row in cur.fetchall()]


def table_fingerprint(conn: psycopg.Connection[Any], schema: str, table: str) -> dict[str, Any]:
    exists = conn.execute("SELECT to_regclass(%s) IS NOT NULL", (f"{schema}.{table}",)).fetchone()[0]
    if not exists:
        return {"table": f"{schema}.{table}", "exists": False}
    ident = sql.SQL("{}.{}").format(sql.Identifier(schema), sql.Identifier(table))
    count = conn.execute(sql.SQL("SELECT count(*) FROM {}").format(ident)).fetchone()[0]
    # Hash fixed-size per-row MD5 digests with SHA-256. This avoids exporting row contents.
    query = sql.SQL(
        "SELECT coalesce(string_agg(row_md5, '' ORDER BY row_md5), '') "
        "FROM (SELECT md5(to_jsonb(t)::text) AS row_md5 FROM {} AS t) q"
    ).format(ident)
    row_digest_stream = conn.execute(query).fetchone()[0]
    digest = hashlib.sha256(row_digest_stream.encode("ascii")).hexdigest()
    algorithm = "sha256(sorted_md5(canonical_postgres_json_rows))"
    return {
        "table": f"{schema}.{table}",
        "exists": True,
        "row_count": count,
        "content_sha256": digest,
        "algorithm": algorithm,
    }


def main() -> None:
    result: dict[str, Any] = {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "probe_mode": "REPEATABLE_READ_READ_ONLY",
        "database_writes": 0,
        "model_runs_started": 0,
    }
    with psycopg.connect(DSN) as conn:
        conn.execute("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        conn.execute("SET LOCAL statement_timeout = '30s'")
        conn.execute("SET LOCAL idle_in_transaction_session_timeout = '60s'")
        result["transaction_read_only"] = conn.execute("SHOW transaction_read_only").fetchone()[0]
        result["transaction_isolation"] = conn.execute("SHOW transaction_isolation").fetchone()[0]

        result["table_fingerprints"] = [table_fingerprint(conn, *item) for item in TABLES]

        profile_cur = conn.execute(
            """
            SELECT p.profile_id, p.disease_slug, p.country_alpha3, p.metric_code, p.cadence,
                   p.period_start, p.period_end, p.observed_count, p.nonzero_count,
                   p.source_count, p.profile_sha256,
                   s.snapshot_id, s.n_obs, s.first_date, s.last_date,
                   s.series_sha256, s.snapshot_sha256
            FROM epidemic_kg.kg_preexecution_data_profiles p
            LEFT JOIN epidemic_kg.kg_data_snapshots s
              ON lower(s.country) = lower(p.country_alpha3)
             AND lower(s.disease) = lower(p.disease_slug)
             AND lower(s.indicator) = lower(p.metric_code)
             AND lower(s.frequency) = lower(p.cadence)
            WHERE upper(p.country_alpha3) = 'BRA'
              AND lower(p.disease_slug) = 'dengue'
            ORDER BY (p.metric_code = 'cases_new') DESC, p.observed_count DESC, p.profile_id
            """
        )
        profiles = rows_as_dicts(profile_cur)
        result["brazil_dengue_profiles"] = profiles

        selected_profile_id = profiles[0]["profile_id"] if profiles else None
        result["selected_profile_id"] = selected_profile_id

        if selected_profile_id:
            candidates_cur = conn.execute(
                """
                SELECT c.candidate_id, c.run_id, c.profile_id, c.blueprint_id, c.bundle_id,
                       c.preexecution_evidence_score, c.parameter_confidence_score,
                       c.evidence_coverage, c.hard_gates, c.rank, c.actually_executed,
                       c.fit_called, c.predict_called, c.local_backtest_status,
                       c.runtime_validation_status, c.forecast_run_id,
                       c.formal_recommendation_eligible, c.promotion_eligible,
                       c.active_pointer_eligible, c.accuracy_claim_allowed,
                       c.explanation_zh, b.display_name, b.model_family, b.is_baseline
                FROM epidemic_kg.kg_preexecution_model_candidates c
                JOIN epidemic_kg.kg_preexecution_model_blueprints b USING (blueprint_id)
                WHERE c.profile_id = %s
                ORDER BY c.rank, c.candidate_id
                """,
                (selected_profile_id,),
            )
            result["selected_preexecution_candidates"] = rows_as_dicts(candidates_cur)

            recs_cur = conn.execute(
                """
                SELECT r.recommendation_id, r.run_id, r.profile_id, r.adapter_id,
                       r.bundle_id, r.is_baseline, r.total_score,
                       r.parameter_confidence_score, r.recomputed_total,
                       r.recomputed_param, r.rank, r.eligibility_status,
                       r.ineligibility_reasons, r.gap_list,
                       ec.contract_id, ec.status AS contract_status,
                       ec.contract_sha256
                FROM epidemic_kg.kg_recommendations r
                LEFT JOIN epidemic_kg.kg_execution_contracts ec
                  ON ec.recommendation_id = r.recommendation_id
                WHERE r.profile_id = %s
                ORDER BY r.rank, r.recommendation_id
                """,
                (selected_profile_id,),
            )
            result["selected_recommendations"] = rows_as_dicts(recs_cur)
        else:
            result["selected_preexecution_candidates"] = []
            result["selected_recommendations"] = []

        shadow_cur = conn.execute(
            """
            SELECT run_id, run_key, mode, status, disease_slug, country_alpha3,
                   metric_code, cadence, data_through, model_name, fallback_reason,
                   promotion_eligible, active_pointer_changed, trusted_rows_changed,
                   created_at, updated_at
            FROM epidemic_kg.kg_forecast_shadow_runs
            WHERE upper(country_alpha3) = 'BRA' AND lower(disease_slug) = 'dengue'
            ORDER BY created_at DESC, run_id
            """
        )
        result["brazil_dengue_shadow_runs"] = rows_as_dicts(shadow_cur)

        formal_cur = conn.execute(
            """
            SELECT id, run_key, disease_slug, country_iso3, target_metric, source_key,
                   status, validation_level, cutoff_week, model_name, champion_model,
                   fallback_reason, dataset_sha256, git_sha, created_at, published_at,
                   retracted_at, is_default_eligible, operational_gate
            FROM epidemic.forecast_runs
            WHERE upper(country_iso3) = 'BRA' AND lower(disease_slug) = 'dengue'
            ORDER BY created_at DESC, id
            """
        )
        result["brazil_dengue_formal_runs"] = rows_as_dicts(formal_cur)

        conn.execute("ROLLBACK")

    canonical = json.dumps(result, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    result["state_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
