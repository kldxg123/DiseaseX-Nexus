from __future__ import annotations

from pathlib import Path

import pytest

from epidemic_core.p1_12.trusted import TrustedIngestionService, sha, stable_uuid

ROOT = Path(__file__).resolve().parents[2]
MIGRATION_22 = (ROOT / "migrations" / "versions" / "0022_p1_12_review_ready_trusted_feeds.py").read_text(
    encoding="utf-8"
)
MIGRATION_23 = (ROOT / "migrations" / "versions" / "0023_p1_12_discovery_transition_guard.py").read_text(
    encoding="utf-8"
)
REVIEW_APP = (ROOT / "src" / "epidemic_core" / "p1_12" / "review_app.py").read_text(encoding="utf-8")
DASHBOARD_APP = (ROOT / "src" / "epidemic_core" / "p1_12" / "dashboard_app.py").read_text(encoding="utf-8")
TRUSTED_APP = (ROOT / "src" / "epidemic_core" / "p1_12" / "trusted.py").read_text(encoding="utf-8")


def test_candidate_preview_does_not_depend_on_published_preview_schema() -> None:
    assert "FROM epidemic_preview" not in REVIEW_APP
    assert "INSERT INTO epidemic_preview" not in REVIEW_APP
    assert "review_candidate_preview_rows_v2" in REVIEW_APP


def test_review_detail_reads_only_restricted_evidence_views() -> None:
    assert "review_discovery_queries_v2" in REVIEW_APP
    assert "FROM epidemic_p1_12.discovery_queries_v2" not in REVIEW_APP
    assert "GRANT SELECT ON epidemic_p1_12.review_discovery_queries_v2 TO epidemicreview" in MIGRATION_23


def test_review_and_dashboard_roles_cannot_write_formal_observations() -> None:
    assert "GRANT ALL" not in MIGRATION_22
    assert "GRANT ALL" not in MIGRATION_23
    assert "INSERT INTO epidemic.normalized_observations" not in REVIEW_APP
    assert "INSERT INTO epidemic.normalized_observations" not in DASHBOARD_APP


def test_review_decision_is_bound_to_version_artifact_and_eligibility() -> None:
    for token in ("candidate_version_id", "artifact_sha256", "transform_version", "eligibility_hash"):
        assert token in MIGRATION_22
    assert "submit_review_decision_v2" in MIGRATION_22


def test_review_ready_is_database_enforced() -> None:
    assert "mark_review_ready_v2" in MIGRATION_22
    assert "review_ready_v2_guard" in MIGRATION_22
    assert "REVIEW_READY_V2_REJECTED" in MIGRATION_22


def test_legacy_empty_shell_candidates_are_append_only_reclassified() -> None:
    assert "legacy_reclassification_audit" in MIGRATION_22
    assert "INVALID_LEGACY_CANDIDATE" in MIGRATION_22
    assert "DELETE FROM epidemic_discovery.source_review_cases" not in MIGRATION_22


def test_transition_function_rejects_illegal_state_changes() -> None:
    assert "ILLEGAL_DISCOVERY_TRANSITION" in MIGRATION_23
    assert "transition_discovery_run_v2" in MIGRATION_23
    assert "REVOKE ALL ON FUNCTION" in MIGRATION_23


def test_trusted_contract_authorization_is_exactly_scoped() -> None:
    for token in (
        "CURATED_BUILTIN_SOURCE",
        "EBOLA_AND_INFLUENZA_ONLY",
        "TRUSTED_OFFICIAL_REPORTED",
        "PROJECT_OWNER_DIRECTIVE",
    ):
        assert token in MIGRATION_22


def test_trusted_daily_idempotency_and_no_change_contract() -> None:
    assert "schedule_id,source_contract_version,scheduled_utc_date" in MIGRATION_22.replace(" ", "")
    assert "SUCCEEDED_NO_CHANGE" in TRUSTED_APP
    assert "snapshot_created" in TRUSTED_APP


def test_influenza_country_selection_prefers_eligible_china() -> None:
    rows = []
    for code, latest in (("PER", "2026-07-27"), ("CHN", "2026-07-20")):
        for week in range(202601, 202609):
            rows.append(
                {
                    "COUNTRY_CODE": code,
                    "COUNTRY_AREA_TERRITORY": code,
                    "ISO_WEEKSTARTDATE": latest,
                    "ISOYW": week,
                    "ILI_CASE": 1,
                }
            )
    assert TrustedIngestionService._select_influenza_country(rows) == ("CHN", "CHN")


def test_influenza_country_selection_requires_eight_person_weeks() -> None:
    with pytest.raises(RuntimeError, match="WHO_FLUID_RECENT_PERSON_SERIES_REQUIRED"):
        TrustedIngestionService._select_influenza_country(
            [
                {
                    "COUNTRY_CODE": "PER",
                    "COUNTRY_AREA_TERRITORY": "Peru",
                    "ISO_WEEKSTARTDATE": "2026-07-20",
                    "ISOYW": 202601,
                    "ILI_CASE": 1,
                }
            ]
        )


def test_weekly_aggregate_does_not_interpolate_or_accept_negative_values() -> None:
    rows = [
        {"ISOYW": 202601, "ILI_CASE": 3},
        {"ISOYW": 202601, "ILI_CASE": 2},
        {"ISOYW": 202603, "ILI_CASE": -9},
    ]
    assert TrustedIngestionService._weekly_aggregate(rows, ("ILI_CASE",)) == {
        202601: {"ILI_CASE": 5},
        202603: {"ILI_CASE": 0},
    }


def test_person_specimen_and_detection_units_are_separate() -> None:
    assert '"ILI_CASE": "influenza_ili_cases"' in TRUSTED_APP
    assert '("influenza_specimens_processed", "specimen")' in TRUSTED_APP
    assert '("influenza_positive_detections", "detection")' in TRUSTED_APP
    assert '"not_person_case_count": True' in TRUSTED_APP


def test_snapshot_identity_material_is_deterministic() -> None:
    material = {"source": "WHO", "rows": [{"date": "2026-01-01", "value": 1}]}
    assert sha(material) == sha(material)
    assert stable_uuid("formal-snapshot", sha(material)) == stable_uuid("formal-snapshot", sha(material))


def test_html_surfaces_escape_dynamic_values_and_use_external_css() -> None:
    assert "html.escape" in REVIEW_APP
    assert "html.escape" in DASHBOARD_APP
    assert "<style>" not in REVIEW_APP
    assert "<style>" not in DASHBOARD_APP
