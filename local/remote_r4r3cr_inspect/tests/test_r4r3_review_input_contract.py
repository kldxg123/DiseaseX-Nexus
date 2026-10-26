from __future__ import annotations

import hashlib
import json

from epidemic_core.forecast_model_kg.r4r3_review_input import (
    canonicalize_evidence,
    classify_review_input,
    contract_payload,
)


def _row(**overrides):
    text = (
        "Methods. We used XGBoost to forecast weekly reported influenza cases "
        "for Peru human patients using national surveillance data. The forecast "
        "horizon was 4 weeks ahead and the training window used historical data "
        "from 2018 to 2025."
    )
    base = {
        "candidate_id": "00000000-0000-0000-0000-000000000001",
        "natural_key": "doc|page|chunk|hash",
        "previous_run_id": "prev",
        "current_run_id": "cur",
        "input_snapshot_sha256": "s" * 64,
        "work_id": "10000000-0000-0000-0000-000000000001",
        "work_identity_state": "ACTIVE_NON_QUARANTINED",
        "document_version_id": "20000000-0000-0000-0000-000000000001",
        "pdf_sha256": "a" * 64,
        "page_id": "30000000-0000-0000-0000-000000000001",
        "page_number": 3,
        "chunk_id": "40000000-0000-0000-0000-000000000001",
        "chunk_index": 1,
        "char_start": 0,
        "char_end": len(text),
        "evidence_text_raw": text,
        "evidence_anchor": text,
        "evidence_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "disease_text": "influenza",
        "model_text": "xgboost",
        "parameter_name": "",
        "parameter_value": "",
        "parameter_unit": "",
        "document_title": "Influenza forecasting in Peru",
        "chunk_text": text,
        "surrounding_context_before": "Methods",
        "surrounding_context_after": "",
        "reverse_locator_verified": True,
    }
    base.update(overrides)
    return base


def test_canonical_evidence_hash_recomputable():
    row = _row()
    decision = classify_review_input(row)
    payload = contract_payload(row, decision)
    assert hashlib.sha256(payload["evidence_text_canonical"].encode()).hexdigest() == payload["evidence_text_sha256"]
    assert canonicalize_evidence("a-\n b") == "ab"


def test_quarantine_cannot_ready():
    decision = classify_review_input(_row(work_identity_state="QUARANTINED_OR_UNRESOLVED"))
    assert decision.classification == "IDENTITY_QUARANTINED"
    assert "work_identity" in decision.missing_requirements


def test_reference_only_cannot_ready():
    text = "References. Smith J. 2020. XGBoost forecasting. doi:10.1/a. https://example.test"
    decision = classify_review_input(
        _row(evidence_text_raw=text, evidence_anchor=text, chunk_text=text, surrounding_context_before="References")
    )
    assert decision.classification == "REFERENCE_ONLY"
    assert decision.relation_type == "CITES_ONLY"


def test_false_baseline_rejected_but_explicit_baseline_allowed_as_non_ready_without_scope():
    false_text = "Baseline characteristics were measured from baseline for patients."
    false_decision = classify_review_input(
        _row(model_text="baseline", evidence_text_raw=false_text, evidence_anchor=false_text, chunk_text=false_text)
    )
    assert false_decision.classification == "REJECTED_NOISE"

    explicit_text = (
        "Methods. We used a seasonal naive baseline model to forecast weekly "
        "reported influenza cases for Peru human patients."
    )
    explicit_decision = classify_review_input(
        _row(
            model_text="baseline",
            evidence_text_raw=explicit_text,
            evidence_anchor=explicit_text,
            chunk_text=explicit_text,
        )
    )
    assert explicit_decision.classification == "NEEDS_SCOPE"
    assert explicit_decision.model_registry_id == "model:seasonal_naive_baseline"


def test_missing_scope_forces_needs_scope_not_fake_ready():
    text = "Methods. We used XGBoost to forecast influenza."
    decision = classify_review_input(
        _row(evidence_text_raw=text, evidence_anchor=text, chunk_text=text, surrounding_context_before="Methods")
    )
    assert decision.classification == "NEEDS_SCOPE"
    assert "scope_geography" in decision.missing_requirements
    assert "scope_horizon" in decision.missing_requirements


def test_complete_scope_allows_ready_model_relation():
    decision = classify_review_input(_row())
    assert decision.classification == "READY_MODEL_RELATION"
    assert decision.missing_requirements == []
    assert decision.source_section_heading != "UNRESOLVED"
    assert all(v.value and v.refs for v in decision.scope.values())


def test_parameter_name_without_value_blocks_parameter_ready():
    decision = classify_review_input(_row(parameter_name="horizon", parameter_value=""))
    assert decision.classification == "NEEDS_PARAMETER_VALUE"


def test_parameter_value_requires_real_value_and_scope():
    decision = classify_review_input(_row(parameter_name="horizon", parameter_value="4 weeks", parameter_unit="weeks"))
    assert decision.classification == "READY_PARAMETER_EVIDENCE"
    payload = contract_payload(
        _row(parameter_name="horizon", parameter_value="4 weeks", parameter_unit="weeks"), decision
    )
    assert json.loads(payload["scope_evidence_refs"])


def test_negated_relation_rejected():
    text = "Methods. We did not use XGBoost to forecast weekly reported influenza cases for Peru human patients."
    decision = classify_review_input(_row(evidence_text_raw=text, evidence_anchor=text, chunk_text=text))
    assert decision.classification == "REJECTED_NOISE"
    assert decision.relation_polarity == "NEGATED_OR_LIMITED"
