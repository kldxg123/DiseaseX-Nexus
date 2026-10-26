from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

from epidemic_core.ebola.fetcher import FetchResult
from epidemic_core.p1_12.discovery import (
    REQUIRED_EVIDENCE_COUNTS,
    SPECS,
    CandidateDiscoveryService,
    CandidateRow,
    review_ready_violations,
    sha,
    stable_uuid,
)


def _spec():
    return SPECS[0]


def _row() -> CandidateRow:
    return CandidateRow(
        country_iso3="UGA",
        country_name="Uganda",
        disease_id="ebola-disease",
        pathogen_variant="BDBV",
        report_date=None,
        source_as_of_date=dt.date(2026, 5, 15),
        period_start=dt.date(2026, 5, 15),
        period_end=dt.date(2026, 5, 15),
        metric_code="ebola_confirmed_cases_cumulative",
        value=1,
        unit="person",
        case_definition="laboratory_confirmed",
        aggregation_type="cumulative",
        geographic_scope="national_reported",
        source_url="https://evd-daily.health.go.ug/api/stats",
        source_row_locator="JSON array index=0; fields=date,cases",
        raw_value="1",
        normalized_value="1",
        source_fields={"date": "2026-05-15", "cases": 1},
        mapping={"cases": "ebola_confirmed_cases_cumulative"},
        quality_flags=("LICENSE_REVIEW_REQUIRED",),
        license_status="REVIEW_REQUIRED",
    )


def _counts() -> dict[str, int]:
    return {name: 1 for name in REQUIRED_EVIDENCE_COUNTS}


def _violations(*, spec=None, row=None, counts=None, artifact: str | None = None):
    return review_ready_violations(
        spec or _spec(),
        counts or _counts(),
        [_row() if row is None else row],
        artifact if artifact is not None else "a" * 64,
        "b" * 64,
    )


def test_complete_country_date_person_count_and_evidence_is_eligible() -> None:
    assert _violations() == ()


@pytest.mark.parametrize(
    ("changed", "expected"),
    [
        ({"country_iso3": ""}, "COUNTRY_REQUIRED"),
        ({"disease_id": ""}, "DISEASE_REQUIRED"),
        ({"requested_metrics": ()}, "REQUESTED_PERSON_METRIC_REQUIRED"),
        ({"source_tier": ""}, "SOURCE_TIER_REQUIRED"),
    ],
)
def test_incomplete_request_context_is_rejected(changed: dict[str, object], expected: str) -> None:
    assert expected in _violations(spec=replace(_spec(), **changed))


def test_missing_time_is_rejected() -> None:
    row = replace(_row(), report_date=None, source_as_of_date=None, period_start=None, period_end=None)
    assert "POSITIVE_PERSON_PREVIEW_ROW_REQUIRED" in _violations(row=row)


@pytest.mark.parametrize("value", [0, -1, None, True])
def test_missing_or_nonpositive_person_value_is_rejected(value: object) -> None:
    row = replace(_row(), value=value)  # type: ignore[arg-type]
    assert "POSITIVE_PERSON_PREVIEW_ROW_REQUIRED" in _violations(row=row)


def test_missing_artifact_is_rejected() -> None:
    assert "ARTIFACT_SHA256_REQUIRED" in _violations(artifact="")


def test_missing_source_row_locator_is_rejected() -> None:
    assert "POSITIVE_PERSON_PREVIEW_ROW_REQUIRED" in _violations(row=replace(_row(), source_row_locator=""))


def test_metadata_directory_cannot_enter_review() -> None:
    counts = _counts()
    counts["candidate_preview_row_count"] = 0
    counts["mappable_person_metric_count"] = 0
    violations = review_ready_violations(_spec(), counts, [], "a" * 64, "b" * 64)
    assert "CANDIDATE_PREVIEW_ROW_COUNT_REQUIRED" in violations
    assert "POSITIVE_PERSON_PREVIEW_ROW_REQUIRED" in violations


def test_specimen_count_cannot_masquerade_as_person() -> None:
    row = replace(_row(), unit="specimen", metric_code="influenza_specimens_processed")
    assert "POSITIVE_PERSON_PREVIEW_ROW_REQUIRED" in _violations(row=row)


@pytest.mark.parametrize(
    "changed",
    [
        {"country_iso3": "COD"},
        {"disease_id": "influenza"},
        {"metric_code": "report_directory_count"},
        {"source_url": "http://example.test/data"},
        {"case_definition": ""},
        {"geographic_scope": ""},
        {"aggregation_type": "unknown"},
    ],
)
def test_semantically_incomplete_preview_row_is_rejected(changed: dict[str, object]) -> None:
    assert "POSITIVE_PERSON_PREVIEW_ROW_REQUIRED" in _violations(row=replace(_row(), **changed))


def test_all_evidence_stage_counts_are_mandatory() -> None:
    for key in REQUIRED_EVIDENCE_COUNTS:
        counts = _counts()
        counts[key] = 0
        assert f"{key.upper()}_REQUIRED" in _violations(counts=counts)


def test_candidate_products_are_independent_and_who_afro_is_regional() -> None:
    assert len({spec.key for spec in SPECS}) == 3
    assert len({spec.publisher for spec in SPECS}) == 3
    assert len({spec.landing_url for spec in SPECS}) == 3
    assert next(spec for spec in SPECS if spec.key == "who-afro-bvd-weekly-v2").source_tier == "WHO_REGIONAL"


def test_eligibility_material_is_deterministic() -> None:
    material = {"artifact": "a" * 64, "preview": "b" * 64, "counts": _counts()}
    assert sha(material) == sha(material)
    assert stable_uuid("candidate-version", sha(material)) == stable_uuid("candidate-version", sha(material))


FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "p1_12"
PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("filename", "expected_sha", "url", "mime", "parser", "expected_rows"),
    [
        (
            "uganda_moh_evd_daily.json",
            "26beac27e36682d097168a5e84454559aa0b9da860fe6f62efef3dbd6e672c31",
            "https://evd-daily.health.go.ug/api/stats",
            "application/json",
            CandidateDiscoveryService._uganda_rows,
            55,
        ),
        (
            "who_afro_bvd_weekly.pdf",
            "9bd0e84a03a884b54458e1d68ffdd1c9141d66133c8372a94d16cf08e04815ea",
            "https://iris.who.int/server/api/core/bitstreams/6eda0efa-73d3-42dd-900b-b75f92b6b301/content",
            "application/pdf",
            CandidateDiscoveryService._afro_rows,
            2,
        ),
        (
            "ukhsa_influenza_weekly.ods",
            "9d7343f73f23e3ba19ca2b72bd225ebc530cedf4c01a2c62d19ed8746adca7bd",
            "https://assets.publishing.service.gov.uk/media/example/weekly-influenza-and-COVID-19-report-data-week-29-2026.ods",
            "application/vnd.oasis.opendocument.spreadsheet",
            CandidateDiscoveryService._ukhsa_rows,
            52,
        ),
    ],
)
def test_live_artifact_fixture_and_adapter_are_deterministic(
    filename: str,
    expected_sha: str,
    url: str,
    mime: str,
    parser,
    expected_rows: int,
) -> None:
    payload = (FIXTURE_ROOT / filename).read_bytes()
    assert hashlib.sha256(payload).hexdigest() == expected_sha
    result = FetchResult(url, 200, mime, payload, expected_sha, None, None, ())
    first, second = parser(result), parser(result)
    assert len(first) == expected_rows
    assert first == second
    assert any(row.unit == "person" and row.value > 0 for row in first)


def test_p1_12_repair_migration_exposes_publication_id_and_filters_p1_10_views() -> None:
    migration = (PROJECT_ROOT / "migrations/versions/0024_p1_12_root_cause_repair.py").read_text(encoding="utf-8")
    assert 'revision = "0024_p1_12_root_cause_repair"' in migration
    assert "p.id publication_id" in migration
    assert "epidemic_p1_12.review_application_bindings" in migration
    assert "dt_notific" in migration
    assert "case_notification_count" in migration
    assert "random" not in migration.lower()
    assert "row_number() publication_id" not in migration.lower()


def test_p1_10_routes_fail_closed_for_p1_12_cases() -> None:
    source = (PROJECT_ROOT / "src/epidemic_core/p1_10/review_app.py").read_text(encoding="utf-8")
    assert "P1_12_CANDIDATE_NOT_ALLOWED_IN_P1_10_PREVIEW" in source
    assert "epidemic_p1_12.review_application_bindings" in source
    assert source.count("_is_p1_12_case(connection, case_id)") >= 2


def test_dashboard_uses_disease_metric_registry_instead_of_only_generic_metrics() -> None:
    script = (PROJECT_ROOT / "src/epidemic_core/dashboard/static/dashboard.p1-7ar-v1.js").read_text(encoding="utf-8")
    assert "metricRegistry" in script
    assert "ebola_confirmed_cases_cumulative" in script
    assert "influenza_ecmo_admissions" in script
    assert 'makeSvg("cases",app.rows,"cases_new")' not in script
    assert 'makeSvg("deaths",app.rows,"deaths_new")' not in script
