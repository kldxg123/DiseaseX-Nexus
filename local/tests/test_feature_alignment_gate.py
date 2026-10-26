from __future__ import annotations

from live_source_search_orchestrator.feature_alignment_gate import (
    build_feature_generation_blocker_summary,
    check_horizon_label_ready,
    check_minimum_time_series_length,
    check_numeric_target_ready,
    check_weekly_alignment_ready,
)


def test_missing_iso_week_blocks_weekly_alignment():
    events = [{"country": "United States", "disease": "measles", "cases": 3, "iso_week": None}]

    result = check_weekly_alignment_ready(events)

    assert result["ready"] is False
    assert result["blocker_type"] == "insufficient_temporal_signal"


def test_less_than_four_weeks_blocks_rolling_features():
    events = [{"iso_week": "2026-W01", "cases": 1}, {"iso_week": "2026-W02", "cases": 2}]

    result = check_minimum_time_series_length(events, min_weeks=4)

    assert result["ready"] is False
    assert result["distinct_week_count"] == 2


def test_missing_numeric_signal_blocks_target_value():
    events = [{"iso_week": "2026-W01", "cases": None, "outbreaks_count": None}]

    result = check_numeric_target_ready(events)

    assert result["ready"] is False
    assert result["blocker_type"] == "insufficient_numeric_signal"


def test_horizon_label_requires_future_weeks():
    events = [{"iso_week": "2026-W01", "cases": 1}]

    result = check_horizon_label_ready(events, horizons=(7, 28))

    assert result["ready"] is False
    assert result["horizons"]["7"]["required_weeks"] == 2


def test_blocker_summary_is_feature_ready_for_long_numeric_series():
    events = [{"iso_week": f"2026-W0{week}", "cases": week} for week in range(1, 6)]

    result = build_feature_generation_blocker_summary(events)

    assert result["eligible"] is True
    assert result["blocker_types"] == []
