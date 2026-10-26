from __future__ import annotations

import json
from pathlib import Path

import pytest

from epidemic_core.p1_9.promotion import (
    DEMO_ENV,
    PRODUCTION_ENV,
    EnvironmentGate,
    PromotionValidationError,
    _validate,
    aggregate_notifications,
    sha,
)


def _valid_validation_row() -> dict[str, object]:
    binding = {
        "candidate_id": "00000000-0000-0000-0000-000000000001",
        "candidate_version_id": "00000000-0000-0000-0000-000000000002",
        "discovery_run_id": "00000000-0000-0000-0000-000000000003",
        "review_case_id": "00000000-0000-0000-0000-000000000004",
        "artifact_sha256": "a" * 64,
        "schema_sha256": "b" * 64,
        "mapping_sha256": "c" * 64,
        "license_evidence_sha256": "d" * 64,
    }
    return {
        "decision": "APPROVED",
        "review_state": "APPROVED",
        "invalidated_at": None,
        "environment": DEMO_ENV,
        "decision_environment": DEMO_ENV,
        "test_only": True,
        "production_usable": False,
        "evidence_json": binding,
        "binding_sha256": sha(binding),
        "candidate_id": binding["candidate_id"],
        "candidate_version_id": binding["candidate_version_id"],
        "discovery_run_id": binding["discovery_run_id"],
        "review_case_id": binding["review_case_id"],
        "artifact_sha256": binding["artifact_sha256"],
        "schema_sha256": binding["schema_sha256"],
        "mapping_sha256": binding["mapping_sha256"],
        "terms_sha256": binding["license_evidence_sha256"],
        "metric_decision": {
            "metric_code": "notification_record_count",
            "unit": "notifications",
            "data_nature": "derived_aggregation",
            "training_eligible": False,
            "national_aggregate": False,
            "source_date_semantics": "notification_date",
        },
        "geographic_scope_decision": {
            "national_aggregate": False,
            "geography_semantics": "notification_location",
        },
        "assessed_license_status": "CC_ATTRIBUTION_REVIEW_REQUIRED",
        "risk_flags": [],
    }


def _gate(environment: str) -> EnvironmentGate:
    return EnvironmentGate(environment, "epidemic_p1_9_demo", Path("q"), Path("r"))


def test_notification_rows_are_deduplicated_and_aggregated_by_actual_location() -> None:
    rows = [
        {"dt_notific": "2026-01-02", "sg_uf_not": "32", "id_municip": "320010", "nu_ano": 2026, "age": 27},
        {"dt_notific": "2026-01-02", "sg_uf_not": "32", "id_municip": "320010", "nu_ano": 2026, "age": 27},
        {"dt_notific": "2026-01-02", "sg_uf_not": "32", "id_municip": "320020", "nu_ano": 2026, "age": 44},
    ]
    actual = aggregate_notifications(json.dumps(rows).encode())
    assert len(actual) == 2
    assert [item["value_numeric"] for item in actual] == [1, 1]
    assert all(item["geography"]["national_aggregate"] is False for item in actual)
    assert all(item["geography"]["geography_semantics"] == "notification_location" for item in actual)
    assert "age" not in json.dumps(actual, default=str)


def test_notification_mapping_never_invents_cases_or_deaths() -> None:
    actual = aggregate_notifications(
        json.dumps([{"dt_notific": "2026-01-02", "sg_uf_not": "32", "id_municip": "320010"}]).encode()
    )
    serialized = json.dumps(actual, default=str)
    assert "notification_record_count" in serialized
    assert "cases_new" not in serialized
    assert "deaths_new" not in serialized
    assert "incidence_rate" not in serialized


@pytest.mark.parametrize(
    "payload",
    [b"not-json", b"{}", json.dumps([{"dt_notific": "", "sg_uf_not": "32", "id_municip": "1"}]).encode()],
)
def test_unsafe_or_unusable_artifacts_are_rejected(payload: bytes) -> None:
    with pytest.raises(PromotionValidationError):
        aggregate_notifications(payload)


def test_notification_aggregation_accepts_verified_parametros_wrapper() -> None:
    payload = json.dumps(
        {
            "parametros": [
                {"dt_notific": "2026-01-03", "sg_uf_not": "17", "id_municip": "170210"},
                {"dt_notific": "2026-01-03", "sg_uf_not": "17", "id_municip": "170210"},
            ]
        }
    ).encode()

    rows = aggregate_notifications(payload)

    assert len(rows) == 1
    assert rows[0]["value_numeric"] == 1
    assert rows[0]["geography"]["geography_semantics"] == "notification_location"


@pytest.mark.parametrize("field", ["artifact_sha256", "schema_sha256", "mapping_sha256", "terms_sha256"])
def test_approval_binding_drift_is_rejected(field: str) -> None:
    row = _valid_validation_row()
    row[field] = "e" * 64
    with pytest.raises(PromotionValidationError, match="APPROVAL_BINDING_MISMATCH"):
        _validate(row, _gate(DEMO_ENV))


def test_unapproved_candidate_is_rejected() -> None:
    row = _valid_validation_row()
    row["decision"] = "CHANGES_REQUESTED"
    with pytest.raises(PromotionValidationError, match="VALID_APPROVAL_REQUIRED"):
        _validate(row, _gate(DEMO_ENV))


def test_test_only_approval_is_rejected_by_production_worker() -> None:
    row = _valid_validation_row()
    row["environment"] = PRODUCTION_ENV
    row["decision_environment"] = PRODUCTION_ENV
    with pytest.raises(PromotionValidationError, match="TEST_APPROVAL_PRODUCTION_FORBIDDEN"):
        _validate(row, _gate(PRODUCTION_ENV))


def test_unknown_license_is_rejected_in_production() -> None:
    row = _valid_validation_row()
    row["environment"] = PRODUCTION_ENV
    row["decision_environment"] = PRODUCTION_ENV
    row["test_only"] = False
    row["production_usable"] = True
    with pytest.raises(PromotionValidationError, match="LICENSE_NOT_VERIFIED"):
        _validate(row, _gate(PRODUCTION_ENV))


def test_unknown_geographic_scope_is_rejected() -> None:
    row = _valid_validation_row()
    row["geographic_scope_decision"] = {"national_aggregate": False}
    with pytest.raises(PromotionValidationError, match="GEOGRAPHY_SCOPE_UNKNOWN"):
        _validate(row, _gate(DEMO_ENV))


def test_notification_count_cannot_be_mapped_to_cases_new() -> None:
    row = _valid_validation_row()
    row["metric_decision"] = {
        **row["metric_decision"],
        "metric_code": "cases_new",
    }
    with pytest.raises(PromotionValidationError, match="MAPPING_REVALIDATION_FAILED"):
        _validate(row, _gate(DEMO_ENV))
