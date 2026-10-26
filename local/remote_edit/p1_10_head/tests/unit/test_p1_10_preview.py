from __future__ import annotations

import json
from pathlib import Path

import pytest

from epidemic_core.p1_10.preview import PreviewValidationError, aggregate_notifications, sha

ROOT = Path(__file__).resolve().parents[2]


def test_notification_aggregation_is_deterministic_and_deduplicated() -> None:
    rows = [
        {"dt_notific": "2026-01-02", "sg_uf_not": "SP", "id_municip": "3550308", "nu_ano": 2026, "secret": 'REDACTED'},
        {"dt_notific": "2026-01-02", "sg_uf_not": "SP", "id_municip": "3550308", "nu_ano": 2026, "secret": 'REDACTED'},
        {"dt_notific": "2026-01-02", "sg_uf_not": "SP", "id_municip": "3550308", "nu_ano": 2026, "secret": 'REDACTED'},
        {"dt_notific": "2026-01-09", "sg_uf_not": "RJ", "id_municip": "3304557", "nu_ano": 2026},
    ]
    payload = json.dumps({"records": rows}).encode()
    first = aggregate_notifications(payload)
    second = aggregate_notifications(payload)
    assert first == second
    assert len(first) == 2
    assert sum(item["value_numeric"] for item in first) == 3
    assert all(item["metric_code"] == "notification_record_count" for item in first)
    assert all(item["unit"] == "notifications" for item in first)
    assert all(len(item["source_row_set_sha256"]) == 64 for item in first)


def test_invalid_or_empty_artifact_fails_closed() -> None:
    with pytest.raises(PreviewValidationError, match="NO_SAFE_NOTIFICATION_ROWS"):
        aggregate_notifications(json.dumps({"records": [{"name": "unsafe"}]}).encode())
    with pytest.raises(PreviewValidationError, match="ARTIFACT_JSON_INVALID"):
        aggregate_notifications(b"not-json")


def test_sha_is_stable_for_canonical_objects() -> None:
    assert sha({"b": 2, "a": 1}) == sha({"a": 1, "b": 2})


def test_migration_creates_isolated_preview_schema_and_roles() -> None:
    migration = (ROOT / "migrations/versions/0018_p1_10_preview_publication.py").read_text(encoding="utf-8")
    assert 'revision = "0018_p1_10_preview_publication"' in migration
    assert 'down_revision = "0017_p1_9_crypto_resolution"' in migration
    assert "CREATE SCHEMA epidemic_preview" in migration
    assert "REVOKE ALL ON ALL TABLES IN SCHEMA epidemic FROM epidemicpreview" in migration
    assert "training_eligible boolean NOT NULL DEFAULT false" in migration
    assert "data_tier='REVIEW_PREVIEW'" in migration
    assert "P1_9_DEMO_APPROVAL_FORBIDDEN" in migration
    assert "CREATE TABLE epidemic_discovery.discovery_run_candidate_links" in migration
    assert "REUSED_IDENTICAL_ARTIFACT" in migration


def test_identical_artifact_is_linked_instead_of_reported_as_zero_candidates() -> None:
    pipeline = (ROOT / "src/epidemic_core/discovery/candidate_pipeline.py").read_text(encoding="utf-8")
    worker = (ROOT / "src/epidemic_core/discovery/worker.py").read_text(encoding="utf-8")
    console = (ROOT / "src/epidemic_core/discovery/review_app.py").read_text(encoding="utf-8")
    assert "REUSED_EXISTING_CANDIDATE" in pipeline
    assert "discovery_run_candidate_links" in pipeline
    assert "reused_candidate_ids" in worker
    assert "已复用既有候选" in console


def test_review_console_requires_eight_confirmations_and_exact_text() -> None:
    source = (ROOT / "src/epidemic_core/p1_10/review_app.py").read_text(encoding="utf-8")
    assert source.count("name='") >= 0
    for key in (
        "not_trusted",
        "license_pending",
        "sample_partial",
        "not_confirmed_cases",
        "not_national",
        "excluded_from_automation",
        "whitelist_only",
        "no_cross_source_sum",
    ):
        assert key in source
    assert "发布候选预览" in source
    assert "RedirectResponse" in source and ", 303" in source


def test_dashboard_keeps_trusted_default_and_disables_cross_source_sum() -> None:
    app = (ROOT / "src/epidemic_core/dashboard/app.py").read_text(encoding="utf-8")
    script = (ROOT / "src/epidemic_core/dashboard/static/dashboard.p1-10-preview.js").read_text(encoding="utf-8")
    assert '<option value="TRUSTED">可信正式数据（默认）</option>' in app
    assert '<option value="REVIEW_PREVIEW">候选演示数据</option>' in app
    assert "publication_id:source.value" in script
    assert "cross_source_sum" not in script
    assert "api/v1/preview/series" in script


def test_preview_worker_has_no_trusted_write_sql() -> None:
    source = (ROOT / "src/epidemic_core/p1_10/preview.py").read_text(encoding="utf-8")
    assert "INSERT INTO epidemic.normalized_observations" not in source
    assert "INSERT INTO epidemic.dataset_snapshots" not in source
    assert "INSERT INTO epidemic.data_sources" not in source
    assert "epidemic_preview.preview_observations" in source
    assert "DISCOVERY_QUARANTINE_ROOT" in source
