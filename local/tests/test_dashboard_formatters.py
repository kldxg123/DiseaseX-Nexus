from __future__ import annotations

from apps.dashboard.formatters import (
    build_time_series_rows,
    feedback_counts,
    minimum_data_summary,
    summarize_risk,
    summarize_source_health,
)


def test_dashboard_formatters_handle_empty_inputs() -> None:
    assert summarize_risk([])["current_risk_level"] == "unknown"
    assert build_time_series_rows([], []) == []
    assert summarize_source_health({})["total"] == 0
    assert feedback_counts({})["feedback_count"] == 0
    assert minimum_data_summary({})["minimum_feature_set"] == []


def test_summarize_risk_uses_latest_prediction_probability() -> None:
    predictions = [
        {
            "target_date": "2026-01-01",
            "model_version": "v1",
            "provenance_json": {"predicted_outbreak_probability": 0.2},
        },
        {
            "target_date": "2026-02-01",
            "model_version": "v2",
            "provenance_json": {"predicted_outbreak_probability": 0.8},
        },
    ]

    summary = summarize_risk(predictions)

    assert summary["current_risk_level"] == "high"
    assert summary["latest_target_date"] == "2026-02-01"
    assert summary["model_version"] == "v2"


def test_build_time_series_rows_sanitizes_sensitive_fields() -> None:
    rows = build_time_series_rows(
        [{"event_date": "2026-01-01", "country": "A", "disease": "B", "cases": 3}],
        [{"target_date": "2026-01-08", "country": "A", "disease": "B", "predicted_value": 4.0}],
    )

    assert rows == [
        {"kind": "event", "date": "2026-01-01", "country": "A", "disease": "B", "value": 3},
        {"kind": "prediction", "date": "2026-01-08", "country": "A", "disease": "B", "value": 4.0},
    ]
