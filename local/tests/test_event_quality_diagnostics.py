from __future__ import annotations

from live_source_search_orchestrator.event_quality_diagnostics import (
    assess_epidemic_event_quality,
    assess_feature_generation_eligibility,
    diagnose_feature_generation_blockers,
)


def test_single_week_numeric_events_are_not_feature_ready():
    events = [
        {"country": "United States", "disease": "measles", "event_date": "2026-01-01", "iso_week": "2026-W01", "cases": 10},
        {"country": "United States", "disease": "measles", "event_date": "2026-01-01", "iso_week": "2026-W01", "cases": 12},
    ]

    quality = assess_epidemic_event_quality(events)
    eligibility = assess_feature_generation_eligibility(events)

    assert quality.quality_status == "insufficient_temporal_signal"
    assert quality.numeric_event_count == 2
    assert quality.feature_ready_event_count == 0
    assert eligibility.eligible is False
    assert "insufficient_temporal_signal" in eligibility.blocker_types


def test_missing_numeric_signal_reports_numeric_blocker():
    events = [{"country": "United States", "disease": "measles", "iso_week": "2026-W01", "cases": None}]

    blockers = diagnose_feature_generation_blockers(events)

    assert "insufficient_numeric_signal" in {item.blocker_type for item in blockers}


def test_long_numeric_series_is_feature_ready():
    events = [
        {"country": "United States", "disease": "measles", "event_date": f"2026-01-{week:02d}", "iso_week": f"2026-W0{week}", "cases": week}
        for week in range(1, 6)
    ]

    quality = assess_epidemic_event_quality(events)

    assert quality.quality_status == "feature_ready"
    assert quality.feature_ready_event_count == 5
