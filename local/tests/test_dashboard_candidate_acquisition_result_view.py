from __future__ import annotations

import json

from apps.dashboard.formatters import compact_acquisition_quality_summary, compact_candidate_acquisition_summary


def test_candidate_acquisition_result_view_hides_response_body() -> None:
    visible = compact_candidate_acquisition_summary(
        {
            "status": "ok",
            "candidate_title": "Official disease table",
            "candidate_domain": "health.example.gov",
            "request_count": 1,
            "raw_documents_saved": 1,
            "epidemic_events_created": 1,
            "model_features_created": 0,
            "event_extraction_status": "event_only",
            "source_registry_status": "pending_review",
            "follow_links": False,
            "files_downloaded": False,
            "full_body_saved": False,
            "response_body": "<html>hidden</html>",
            "html": "<html>hidden</html>",
        }
    )

    rendered = json.dumps(visible, ensure_ascii=False).lower()
    assert visible["request_count"] == 1
    assert visible["raw_documents_saved"] == 1
    assert visible["epidemic_events_created"] == 1
    assert "response_body" not in rendered
    assert "<html" not in rendered


def test_acquisition_quality_result_view_shows_blockers_not_raw_payloads() -> None:
    visible = compact_acquisition_quality_summary(
        {
            "quality_report": {
                "status": "limited",
                "raw_documents_count": 1,
                "epidemic_events_count": 0,
                "model_features_count": 0,
                "feature_ready_event_count": 0,
                "blocker_types": ["insufficient_temporal_signal", "requires_structured_dataset"],
                "recommended_next_action": "search structured time-series source",
                "raw_text": "hidden",
            }
        }
    )

    rendered = json.dumps(visible, ensure_ascii=False).lower()
    assert visible["status"] == "limited"
    assert "requires_structured_dataset" in visible["blocker_types"]
    assert "raw_text" not in rendered
