from __future__ import annotations

import hashlib
import json
import os

import psycopg

from epidemic_core.forecast_model_kg.r4r3_review_input import (
    canonicalize_evidence,
    classify_review_input,
    contract_payload,
)


def _conn():
    return psycopg.connect(
        dbname=os.environ["R4R3_TEST_DB"],
        user="postgres",
        host="USER_HOME/projects/openclaw/runtime/postgres-run",
        port=55432,
    )


BASE = {
    "candidate_id": "00000000-0000-0000-0000-000000000001",
    "natural_key": "doc|page|chunk|hash",
    "work_id": "00000000-0000-0000-0000-000000000002",
    "work_identity_state": "ACTIVE_NON_QUARANTINED",
    "document_version_id": "00000000-0000-0000-0000-000000000003",
    "pdf_sha256": "a" * 64,
    "page_id": "00000000-0000-0000-0000-000000000004",
    "page_number": 1,
    "chunk_id": "00000000-0000-0000-0000-000000000005",
    "chunk_index": 1,
    "char_start": 0,
    "char_end": 100,
    "reverse_locator_verified": True,
    "document_title": "Influenza forecasting study",
    "disease_text": "influenza",
    "model_text": "GRU",
    "parameter_name": "",
    "parameter_value": "",
    "parameter_unit": "",
}


def test_canonical_evidence_hash_is_recomputable_from_delivered_text():
    raw = "fore-\n casting   influenza with a GRU"
    canonical = canonicalize_evidence(raw)
    assert canonical == "forecasting influenza with a GRU"
    row = {**BASE, "evidence_text_raw": raw, "evidence_anchor": raw}
    decision = classify_review_input(row)
    payload = contract_payload(row, decision)
    assert hashlib.sha256(payload["evidence_text_canonical"].encode()).hexdigest() == payload["evidence_text_sha256"]


def test_quarantined_work_cannot_be_ready():
    row = {
        **BASE,
        "work_identity_state": "QUARANTINED_OR_UNRESOLVED",
        "evidence_text_raw": "We evaluate a GRU model for influenza forecasting.",
        "evidence_anchor": "We evaluate a GRU model for influenza forecasting.",
    }
    assert classify_review_input(row).classification == "IDENTITY_QUARANTINED"


def test_reference_only_cannot_be_ready():
    row = {
        **BASE,
        "document_title": "References",
        "evidence_text_raw": "12. Smith J. influenza forecasting with GRU. Nature 2020. https://doi.org/10.1/test",
        "evidence_anchor": "12. Smith J. influenza forecasting with GRU. Nature 2020. https://doi.org/10.1/test",
    }
    got = classify_review_input(row)
    assert got.classification == "REFERENCE_ONLY"
    assert got.relation_type == "CITES_ONLY"


def test_false_baseline_is_noise_but_registered_seasonal_naive_is_allowed():
    noise = {
        **BASE,
        "model_text": "baseline",
        "evidence_text_raw": "cases were compared with the baseline year probability",
        "evidence_anchor": "cases were compared with the baseline year probability",
    }
    assert classify_review_input(noise).classification == "REJECTED_NOISE"
    allowed = {
        **BASE,
        "model_text": "baseline",
        "evidence_text_raw": "We compare influenza forecasts with a seasonal naive baseline model.",
        "evidence_anchor": "We compare influenza forecasts with a seasonal naive baseline model.",
    }
    got = classify_review_input(allowed)
    assert got.model_registry_id == "model:seasonal_naive_baseline"
    assert got.classification == "READY_MODEL_RELATION"


def test_parameter_name_without_value_blocks_parameter_ready():
    row = {
        **BASE,
        "parameter_name": "window",
        "parameter_value": "",
        "evidence_text_raw": "We evaluate a GRU model for influenza forecasting with a rolling forecast window.",
        "evidence_anchor": "We evaluate a GRU model for influenza forecasting with a rolling forecast window.",
    }
    got = classify_review_input(row)
    assert got.classification == "NEEDS_PARAMETER_VALUE"


def test_live_candidate_contract_denominator_and_ready_gates():
    with _conn() as c, c.cursor() as cur:
        cur.execute("select count(*) from epidemic_kg.kg_pending_claim_candidates where candidate_generation='R4_STRUCTURED'")
        assert cur.fetchone()[0] == 296
        cur.execute(
            """
            select count(*) from epidemic_kg.kg_r4r3_candidate_review_inputs
            where classification like 'READY_%' and work_identity_state <> 'ACTIVE_NON_QUARANTINED'
            """
        )
        assert cur.fetchone()[0] == 0
        cur.execute(
            """
            select char_start, char_end, evidence_text_canonical, evidence_text_sha256
            from epidemic_kg.kg_r4r3_candidate_review_inputs
            where classification like 'READY_%'
            """
        )
        for start, end, canonical, digest in cur.fetchall():
            assert start is not None and end is not None
            assert hashlib.sha256(canonical.encode()).hexdigest() == digest


def test_live_zero_impact_gates():
    with _conn() as c, c.cursor() as cur:
        cur.execute("select count(*) from epidemic_kg.kg_pending_claim_candidates where coalesce(recommendation_eligible,false)=true")
        assert cur.fetchone()[0] == 0
        cur.execute("select count(*) from epidemic_kg.kg_claims where coalesce(properties->>'paper1_participation','0')='1'")
        assert cur.fetchone()[0] == 0
        cur.execute("select count(*) from epidemic_kg.kg_forecast_shadow_runs where coalesce(promotion_eligible,false)=true or coalesce(active_pointer_changed,false)=true")
        assert cur.fetchone()[0] == 0
