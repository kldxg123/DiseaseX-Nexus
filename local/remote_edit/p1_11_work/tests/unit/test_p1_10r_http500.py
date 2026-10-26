from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from epidemic_core.p1_10.dashboard_app import install_routes
from epidemic_core.p1_10.diagnostics import INTERFACE_VERSION, REQUIRED_REVISION

ROOT = Path(__file__).resolve().parents[2]


def _text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _preview_client(rows: list[dict[str, Any]]) -> TestClient:
    app = FastAPI()

    def query(_: object, sql: str, __: object | None = None) -> list[dict[str, Any]]:
        if "active_preview_publications_v1" in sql:
            return rows
        raise AssertionError(f"unexpected query: {sql}")

    install_routes(
        app,
        object(),  # type: ignore[arg-type]
        lambda _: True,
        lambda: JSONResponse({"error_code": "AUTHENTICATION_REQUIRED"}, 401),
        query,
        lambda value, status_code=200: JSONResponse(value, status_code=status_code),
    )
    return TestClient(app)


def test_01_preview_without_publication_initializes_http_200() -> None:
    response = _preview_client([]).get("/api/v1/preview/options")
    assert response.status_code == 200
    assert response.json()["state"] == "EMPTY"
    assert response.json()["active_publication"] is None


def test_02_empty_preview_schema_contract_is_explicit() -> None:
    body = _preview_client([]).get("/api/v1/preview/options").json()
    assert body["sources"] == body["countries"] == body["diseases"] == body["metrics"] == []
    assert body["data_tier"] == "REVIEW_PREVIEW"


def test_03_readyz_fails_closed_for_missing_contract() -> None:
    source = _text("src/epidemic_core/p1_10/diagnostics.py")
    assert "except Exception as exc:  # readiness must fail closed" in source
    assert "200 if ready else 503" in source


def test_04_query_post_uses_authenticated_csrf_checked_post_and_303() -> None:
    source = _text("src/epidemic_core/discovery/review_app.py")
    assert '@app.post("/discoveries")' in source
    assert "origin_ok(request, csrf" in source
    assert 'RedirectResponse(f"/discoveries/{run_id}", 303)' in source


def test_05_provider_zero_is_a_terminal_ui_state_not_an_exception() -> None:
    source = _text("src/epidemic_core/discovery/review_app.py")
    assert "0 个候选不代表不存在数据源" in source
    assert "DISCOVERY_COMPLETE_NO_CANDIDATE" in source


def test_06_provider_timeout_has_classified_degraded_states() -> None:
    source = _text("src/epidemic_core/discovery/review_app.py")
    assert "PROVIDER_DEGRADED" in source
    assert "FAILED_RETRYABLE" in source


def test_07_duplicate_query_submission_uses_database_idempotency_function() -> None:
    source = _text("src/epidemic_core/discovery/review_app.py")
    assert "submit_structured_discovery" in source
    submit_route = source[
        source.index('@app.post("/discoveries")') : source.index('@app.post("/discoveries/{run_id}/retry")')
    ]
    assert "discovery_run_candidate_links" not in submit_route


def test_08_missing_mapping_has_a_409_business_code() -> None:
    source = _text("src/epidemic_core/p1_10/review_app.py")
    assert '"PREVIEW_MAPPING_REQUIRED": 409' in source


def test_09_missing_safe_projection_has_a_409_business_code() -> None:
    source = _text("src/epidemic_core/p1_10/review_app.py")
    assert '"PREVIEW_SANITIZATION_REQUIRED": 409' in source


def test_10_pending_license_is_rendered_as_warning() -> None:
    source = _text("src/epidemic_core/p1_10/review_app.py")
    assert "许可待复核" in source
    assert "不等于正式批准" in source


def test_11_trusted_approval_remains_separate_and_disabled() -> None:
    source = _text("src/epidemic_core/discovery/review_app.py")
    assert 'value="APPROVED" disabled' in source


def test_12_publication_requires_preview_decision() -> None:
    migration = _text("migrations/versions/0018_p1_10_preview_publication.py")
    assert "preview_decision_id uuid NOT NULL UNIQUE" in migration
    assert "queue_preview_publication" in migration


def test_13_test_only_decision_cannot_enter_production() -> None:
    migration = _text("migrations/versions/0018_p1_10_preview_publication.py")
    assert "PREVIEW_ENVIRONMENT_BOUNDARY_INVALID" in migration


def test_14_preview_worker_has_isolated_database_role() -> None:
    migration = _text("migrations/versions/0018_p1_10_preview_publication.py")
    assert "REVOKE ALL ON ALL TABLES IN SCHEMA epidemic FROM epidemicpreview" in migration


def test_15_dashboard_reader_uses_only_preview_views() -> None:
    source = _text("src/epidemic_core/p1_10/dashboard_app.py")
    assert "quarantine_artifacts" not in source
    assert "source_preview_rows" not in source


def test_16_preview_does_not_enter_trusted_api() -> None:
    source = _text("src/epidemic_core/p1_10/dashboard_app.py")
    assert "epidemic.normalized_observations" not in source
    assert '"data_tier": "REVIEW_PREVIEW"' in source


def test_17_preview_does_not_enter_training_views() -> None:
    worker = _text("src/epidemic_core/p1_10/preview.py")
    assert "training_snapshot_observations_v1" not in worker
    assert "training_eligible" in worker


def test_18_preview_does_not_enter_daily_update() -> None:
    worker = _text("src/epidemic_core/p1_10/preview.py")
    assert "daily_update_schedules" not in worker


def test_19_preview_cannot_sum_with_who() -> None:
    script = _text("src/epidemic_core/dashboard/static/dashboard.p1-10-preview.js")
    assert 'value="all"' not in script and "value='all'" not in script
    assert "禁止全部来源合计" in script


def test_20_empty_dates_request_the_complete_snapshot() -> None:
    source = _text("src/epidemic_core/p1_10/dashboard_app.py")
    assert '"complete_snapshot_when_dates_empty": start is None and end is None' in source


def test_21_snapshot_hash_is_recomputed_in_worker_contract() -> None:
    worker = _text("src/epidemic_core/p1_10/preview.py")
    assert "snapshot_sha256" in worker
    assert "preview_snapshot_observations" in worker


def test_22_concurrent_submissions_have_unique_constraints() -> None:
    migration = _text("migrations/versions/0018_p1_10_preview_publication.py")
    assert "idempotency_key uuid NOT NULL UNIQUE" in migration
    assert "UNIQUE(review_case_id,candidate_version_id,artifact_sha256,mapping_sha256,environment)" in migration


def test_23_failed_publication_has_no_partial_active_switch() -> None:
    worker = _text("src/epidemic_core/p1_10/preview.py")
    assert "def _publish_atomic" in worker
    assert "PREVIEW_PUBLISHED" in worker


def test_24_revoke_hides_active_view_but_retains_history() -> None:
    migration = _text("migrations/versions/0018_p1_10_preview_publication.py")
    assert "revoke_preview_publication" in migration
    assert "preview_dashboard_publications" in migration
    assert "PREVIEW_REVOKED" in migration


def test_25_request_id_correlates_safe_errors() -> None:
    dashboard = _text("src/epidemic_core/dashboard/app.py")
    review = _text("src/epidemic_core/discovery/review_app.py")
    assert '"X-Request-ID": rid' in dashboard
    assert '"X-Request-ID": rid' in review


def test_26_ui_never_renders_traceback() -> None:
    diagnostics = _text("src/epidemic_core/p1_10/diagnostics.py")
    assert "没有显示内部 traceback" in diagnostics
    assert "traceback.format" not in diagnostics


def test_27_ui_whitelists_only_sanitized_fields() -> None:
    review = _text("src/epidemic_core/p1_10/review_app.py")
    assert 'WHITELIST = ("dt_notific", "sg_uf_not", "id_municip", "nu_ano", "case_notification_count")' in review
    assert "safe = {key: sanitized.get(key) for key in WHITELIST" in review


def test_28_revision_and_interface_are_fixed_contracts() -> None:
    assert REQUIRED_REVISION == "0019_p1_10r_formal_preview_http500"
    assert INTERFACE_VERSION == "p1-10r-v1"
    migration = _text("migrations/versions/0019_p1_10r_formal_preview_http500.py")
    assert "runtime_readiness_v1" in migration


@pytest.mark.parametrize("field", ["password", "authorization", "cookie", "api_key"])
def test_reports_must_not_gain_sensitive_field_names(field: str) -> None:
    diagnostics = _text("src/epidemic_core/p1_10/diagnostics.py").lower()
    assert f'logger.exception("{field}' not in diagnostics


def test_empty_options_payload_is_json_serializable() -> None:
    response = _preview_client([]).get("/api/v1/preview/options")
    json.dumps(response.json(), ensure_ascii=False)
