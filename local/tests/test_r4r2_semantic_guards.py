from __future__ import annotations

import os

import psycopg

from epidemic_core.forecast_model_kg.r4_shadow import ForecastOrchestrator, RecommendationProvider, stable_uuid
from epidemic_core.forecast_model_kg.r4r2_semantic import classify_candidate_semantic


def _conn():
    return psycopg.connect(
        dbname=os.environ["R4R2_TEST_DB"],
        user="postgres",
        host="USER_HOME/projects/openclaw/runtime/postgres-run",
        port=55432,
    )


def test_semantic_classifier_rejects_generic_baseline_noise():
    row = {
        "disease_text": "COVID",
        "model_text": "baseline",
        "parameter_name": "window",
        "parameter_value": "",
        "evidence_text": "a fold change from baseline of at least 10.0 was used for mutation filtering",
        "document_title": "Variant surveillance study",
        "reverse_locator_verified": True,
    }
    got = classify_candidate_semantic(row)
    assert got.disposition == "REJECTED_NOISE"
    assert not got.review_ready


def test_semantic_classifier_accepts_forecast_model_relation_not_parameter_value():
    row = {
        "disease_text": "influenza",
        "model_text": "GRU",
        "parameter_name": "",
        "parameter_value": "",
        "evidence_text": "We evaluate a GRU model for influenza forecasting and compare forecast accuracy across four seasons.",
        "document_title": "Neural network models for influenza forecasting",
        "reverse_locator_verified": True,
    }
    got = classify_candidate_semantic(row)
    assert got.disposition == "READY_MODEL_RELATION"
    assert got.review_ready


def test_semantic_classifier_requires_source_parameter_value():
    row = {
        "disease_text": "COVID",
        "model_text": "LSTM",
        "parameter_name": "window",
        "parameter_value": "",
        "evidence_text": "The LSTM forecast model was evaluated for COVID cases using a rolling forecast window.",
        "document_title": "COVID forecasting",
        "reverse_locator_verified": True,
    }
    got = classify_candidate_semantic(row)
    assert got.disposition == "NEEDS_PARAMETER_VALUE"
    assert "parameter_value" in got.missing_fields


def test_approved_kg_isolation_fixture_positive_and_negative_paths():
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute("begin")
            cur.execute("select node_id from epidemic_kg.kg_nodes where node_type='WORK' and quarantine=false limit 1")
            work = cur.fetchone()[0]
            cur.execute("select node_id from epidemic_kg.kg_nodes where node_type='DOCUMENT_VERSION' limit 1")
            doc = cur.fetchone()[0]
            base = "r4r2-pytest-fixture"
            positive_id = stable_uuid(base, "positive")
            pending_id = stable_uuid(base, "pending")
            paper1_id = stable_uuid(base, "paper1")
            rows = [
                (positive_id, "claim:r4r2:pytest:positive", "VALIDATED", True, False, {}),
                (pending_id, "claim:r4r2:pytest:pending", "PENDING_REVIEW", True, False, {}),
                (paper1_id, "claim:r4r2:pytest:paper1", "VALIDATED", True, False, {"paper1_participation": "1"}),
            ]
            for claim_id, key, review_status, eligible, quarantine, props in rows:
                cur.execute(
                    """
                    insert into epidemic_kg.kg_claims
                    (claim_id,canonical_key,work_node_id,document_node_id,claim_category,claim_status,
                     subject_text,predicate_text,object_text,review_status,evidence_eligible,quarantine,
                     metric_name,forecast_horizon,properties)
                    values(%s,%s,%s,%s,'MODEL_PARAMETER','DIRECTLY_REPORTED',
                           'dengue cases_new','EVALUATES_MODEL','rolling_mean_baseline',
                           %s,%s,%s,'cases_new','4 weeks',%s::jsonb)
                    """,
                    (claim_id, key, work, doc, review_status, eligible, quarantine, __import__("json").dumps(props)),
                )
            provider = RecommendationProvider(c)
            hits = provider.approved_executable_kg_candidates("dengue", "cases_new")
            hit_keys = {h["canonical_key"] for h in hits}
            assert "claim:r4r2:pytest:positive" in hit_keys
            assert "claim:r4r2:pytest:pending" not in hit_keys
            assert "claim:r4r2:pytest:paper1" not in hit_keys
            assert not provider.approved_executable_kg_candidates("nipah", "cases_new")
            cur.execute("rollback")


def test_shadow_idempotency_and_no_future_label_leakage():
    with _conn() as c:
        orch = ForecastOrchestrator(c, token_prefix="r4r2_pytest")
        first = orch.run("BRA", "dengue", "week", "cases_new", "SHADOW")
        second = orch.run("BRA", "dengue", "week", "cases_new", "SHADOW")
        assert first["run_id"] == second["run_id"]
        with c.cursor() as cur:
            cur.execute(
                """
                select count(*), count(*) filter (where row_type='FUTURE' and actual_value is not null),
                       count(*) filter (where row_type='FUTURE' and is_observed)
                from epidemic_kg.kg_forecast_shadow_values
                where run_id=%s
                """,
                (first["run_id"],),
            )
            count, future_actual, future_observed = cur.fetchone()
            assert count == first["forecast_output_row_count"]
            assert future_actual == 0
            assert future_observed == 0
        c.rollback()
