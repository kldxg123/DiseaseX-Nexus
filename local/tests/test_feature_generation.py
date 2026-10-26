from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from cleaner_agent.feature_engineering import (
    aggregate_events_weekly,
    build_features_from_weekly_aggregates,
)


FIXTURE = Path("tests/fixtures/feature_events_norovirus.json")


def _aggregates():
    events = [SimpleNamespace(**row) for row in json.loads(FIXTURE.read_text(encoding="utf-8"))]
    return aggregate_events_weekly(events, fill_missing_weeks=True)


def test_feature_generation_targets_lags_and_rolling_without_future_leakage() -> None:
    rows = build_features_from_weekly_aggregates(
        _aggregates(),
        horizons_days=(7, 28),
        outbreak_threshold=3,
    )
    by_key = {(row["iso_week"], row["horizon_days"]): row for row in rows}

    week_5 = by_key[("2025-W05", 7)]
    assert week_5["target_value"] == 6.0
    assert week_5["target_outbreak_label"] is False
    assert week_5["feature_json"]["current_week_cases"] == 5
    assert week_5["feature_json"]["lag_1w_cases"] == 4
    assert week_5["feature_json"]["lag_2w_cases"] == 0
    assert week_5["feature_json"]["lag_4w_cases"] == 1
    assert week_5["feature_json"]["rolling_4w_mean_cases"] == 3.0
    assert "season_week_sin" in week_5["feature_json"]
    assert "season_week_cos" in week_5["feature_json"]
    assert week_5["feature_json"]["holiday_flag"] is False
    json.dumps(week_5["feature_json"])


def test_feature_generation_28_day_target_and_tail_skip() -> None:
    rows = build_features_from_weekly_aggregates(
        _aggregates(),
        horizons_days=(28,),
        outbreak_threshold=3,
    )
    by_week = {row["iso_week"]: row for row in rows}

    assert by_week["2025-W05"]["target_value"] == 22.0
    assert by_week["2025-W05"]["target_outbreak_label"] is True
    assert "2025-W09" not in by_week
    assert "2025-W12" not in by_week
