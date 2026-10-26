from __future__ import annotations

import json

from apps.dashboard.formatters import sanitize_dashboard_payload
from apps.dashboard.pages import (
    _deepseek_3d_summary,
    _deepseek_feature_eligibility_summary,
    _deepseek_human_review_summary,
    _deepseek_readiness_rows,
)


def test_deepseek_result_view_hides_prompt_completion_and_raw_text() -> None:
    visible = sanitize_dashboard_payload(
        {
            "status": "ok",
            "deepseek_call_count": 1,
            "records_extracted": 8,
            "records_persisted": 8,
            "prompt": "hidden prompt",
            "completion": "hidden completion",
            "raw_text": "hidden raw text",
            "response_body": "hidden response",
        }
    )

    rendered = json.dumps(visible, ensure_ascii=False).lower()
    assert visible["deepseek_call_count"] == 1
    for forbidden in ("prompt", "completion", "raw_text", "response_body", "hidden"):
        assert forbidden not in rendered


def test_deepseek_readiness_rows_are_review_oriented() -> None:
    rows = _deepseek_readiness_rows(
        [
            {
                "record_id": "r1",
                "disease": "influenza",
                "country": "United States",
                "source_domain": "example.gov",
                "readiness_class": "evidence_only",
                "event_ready": False,
                "feature_ready": False,
                "visualization_ready": True,
                "missing_fields": ["event_date", "metric_value"],
                "blocker_types": ["requires_human_review"],
                "recommended_action": "review source evidence",
                "prompt": "hidden",
                "completion": "hidden",
            }
        ]
    )

    rendered = json.dumps(rows, ensure_ascii=False).lower()
    assert rows[0]["readiness_class"] == "evidence_only"
    assert "requires_human_review" in rows[0]["blocker_types"]
    assert "prompt" not in rendered
    assert "completion" not in rendered


def test_deepseek_human_review_and_3d_summaries_are_compact() -> None:
    review = _deepseek_human_review_summary(
        {
            "status": "ok",
            "result": {
                "imported_annotations": 8,
                "approved_event_count": 0,
                "blocked_count": 8,
                "promoted_epidemic_events": 0,
                "feature_generation_allowed": False,
            },
            "lesson_json": {"hidden": True},
        }
    )
    eligibility = _deepseek_feature_eligibility_summary({"result": {"feature_generation_allowed": False}})
    visual = _deepseek_3d_summary({"disease": "influenza", "country": "United States", "points": [{"x": 1}]})

    rendered = json.dumps({"review": review, "eligibility": eligibility, "visual": visual}, ensure_ascii=False).lower()
    assert review["blocked_count"] == 8
    assert eligibility["feature_generation_allowed"] is False
    assert visual["point_count"] == 1
    assert "lesson_json" not in rendered
