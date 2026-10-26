from __future__ import annotations

import json

from apps.dashboard.formatters import (
    compact_candidate_acquisition_summary,
    compact_live_source_search_summary,
    rows_from_live_source_search_provider_diagnostics,
    rows_from_live_source_search_review_queue,
    sanitize_dashboard_payload,
)


def test_dashboard_result_formatters_extract_visible_summary() -> None:
    payload = {
        "status": "ok",
        "country": "United States",
        "disease": "influenza",
        "request_count_total": 2,
        "candidates_seen": 4,
        "candidates_after_dedupe": 3,
        "pending_review_inserted": 3,
        "connector_specs_generated": 2,
        "raw_response": "hidden",
        "metadata_json": {"raw": "hidden"},
    }

    summary = compact_live_source_search_summary(payload)

    assert summary["status"] == "ok"
    assert summary["candidates_after_dedupe"] == 3
    rendered = json.dumps(summary, ensure_ascii=False).lower()
    assert "raw_response" not in rendered
    assert "metadata_json" not in rendered


def test_dashboard_acquisition_formatter_is_result_oriented() -> None:
    summary = compact_candidate_acquisition_summary(
        {
            "status": "limited",
            "candidate_domain": "www.example.gov",
            "request_count": 1,
            "raw_documents_saved": 1,
            "epidemic_events_created": 0,
            "model_features_created": 0,
            "response_body": "<html>do not show</html>",
            "full_body_saved": False,
        }
    )

    assert summary["status"] == "limited"
    assert summary["raw_documents_saved"] == 1
    assert "response_body" not in json.dumps(summary, ensure_ascii=False).lower()


def test_dashboard_provider_and_review_rows_are_safe() -> None:
    payload = {
        "provider_failure_diagnostics": [
            {
                "provider": "data_gov",
                "status": "ok",
                "request_count": 1,
                "raw_response": "hidden",
            }
        ],
        "review_queue": [
            {
                "title": "Influenza data",
                "domain": "catalog.data.gov",
                "source_score": 0.87,
                "metadata_json": {"raw": "hidden"},
                "registry_status": "pending_review",
            }
        ],
    }

    provider_rows = rows_from_live_source_search_provider_diagnostics(payload)
    review_rows = rows_from_live_source_search_review_queue(payload)

    rendered = json.dumps({"provider": provider_rows, "review": review_rows}, ensure_ascii=False).lower()
    assert provider_rows[0]["provider"] == "data_gov"
    assert review_rows[0]["registry_status"] == "pending_review"
    assert "raw_response" not in rendered
    assert "metadata_json" not in rendered


def test_sanitize_dashboard_payload_removes_raw_and_secret_fields() -> None:
    safe = sanitize_dashboard_payload(
        {
            "status": "ok",
            "api_key": 'REDACTED',
            "token": "do-not-show",
            "prompt": "do-not-show",
            "completion": "do-not-show",
            "response_body": "do-not-show",
            "raw_text": "do-not-show",
            "nested": {"feature_json": {"x": 1}, "visible_count": 3},
        }
    )

    rendered = json.dumps(safe, ensure_ascii=False).lower()
    assert safe["status"] == "ok"
    assert safe["nested"]["visible_count"] == 3
    for forbidden in (
        "api_key",
        "token",
        "prompt",
        "completion",
        "response_body",
        "raw_text",
        "feature_json",
        "do-not-show",
    ):
        assert forbidden not in rendered
