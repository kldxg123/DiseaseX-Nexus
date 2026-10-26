from __future__ import annotations

import json

from apps.dashboard.formatters import (
    compact_current_risk_summary,
    compact_feedback_summary,
    compact_source_health_summary,
    format_nullable_date,
    format_risk_level,
    rows_from_recent_events_and_predictions,
)


def test_compact_current_risk_summary_formats_missing_prediction_date() -> None:
    summary = compact_current_risk_summary(
        {
            "current_risk_level": "critical",
            "latest_prediction_date": None,
            "latest_target_date": "2025-05-26",
            "model_version": "baseline:moving_average:h28:v1",
        }
    )

    assert summary["current_risk_level"] == "严重 Critical"
    assert summary["latest_prediction_date"] == "暂无"
    assert summary["latest_target_date"] == "2025-05-26"
    assert summary["model_version"] == "baseline:moving_average:h28:v1"
    assert summary["status_note"] == "已有预测记录，但缺少预测日期"
    assert "null" not in json.dumps(summary, ensure_ascii=False).lower()


def test_overview_formatters_create_table_rows() -> None:
    source_rows = compact_source_health_summary(
        {"total": 8, "by_status": {"active": 2, "pending_review": 3, "degraded": 1, "disabled": 2}}
    )
    feedback_rows = compact_feedback_summary(
        {
            "feedback_count": 5,
            "by_status": {"success": 1, "partial_success": 2, "failure": 1, "unknown": 1},
        }
    )

    assert source_rows == [
        {"status": "已启用 active", "count": 2},
        {"status": "待审核 pending_review", "count": 3},
        {"status": "降级 degraded", "count": 1},
        {"status": "禁用 disabled", "count": 2},
    ]
    assert feedback_rows[0] == {"verdict": "反馈总数", "count": 5}
    assert feedback_rows[-1] == {"verdict": "未知 unknown", "count": 1}


def test_recent_event_and_prediction_rows_are_safe_and_compact() -> None:
    rows = rows_from_recent_events_and_predictions(
        [
            {
                "country": "United States",
                "disease": "norovirus",
                "iso_week": "2025-W21",
                "cases": 12,
                "outbreaks_count": 2,
                "confidence_score": 0.9,
                "metadata_json": {"token": "hidden"},
                "raw_text": "do-not-show",
            }
        ],
        [
            {
                "target_date": "2025-05-26",
                "predicted_value": 14.0,
                "risk_level": "medium",
                "model_version": "baseline:v1",
                "provenance_json": {"predicted_outbreak_probability": 0.61, "secret": 'REDACTED'},
            }
        ],
    )

    rendered = json.dumps(rows, ensure_ascii=False).lower()
    assert rows["events_rows"][0]["iso_week"] == "2025-W21"
    assert rows["prediction_rows"][0]["risk_level"] == "中 Medium"
    assert rows["prediction_rows"][0]["probability"] == 0.61
    assert "metadata_json" not in rendered
    assert "raw_text" not in rendered
    assert "secret" not in rendered
    assert "token" not in rendered


def test_basic_overview_value_formatters() -> None:
    assert format_risk_level("HIGH") == "高 High"
    assert format_risk_level("moderate") == "中 Medium"
    assert format_risk_level(None) == "未知 Unknown"
    assert format_nullable_date(None) == "暂无"
    assert format_nullable_date("null") == "暂无"
