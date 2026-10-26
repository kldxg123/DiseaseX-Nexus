from __future__ import annotations

from datetime import date, timedelta

from predictor_agent.dataset import FeatureRow
from predictor_agent.minimum_data_analyzer import (
    get_default_feature_groups,
    keep_last_n_weeks,
    run_learning_curve,
    select_feature_groups,
)


def _rows(count: int = 20) -> list[FeatureRow]:
    start = date(2025, 1, 6)
    rows: list[FeatureRow] = []
    for index in range(count):
        value = float(index + 1)
        rows.append(
            FeatureRow(
                id=index + 1,
                country="United States",
                admin1=None,
                disease="norovirus",
                target_date=start + timedelta(days=7 * index),
                iso_week=f"2025-W{index + 1:02d}",
                horizon_days=7,
                feature_json={
                    "current_week_cases": value,
                    "lag_1w_cases": value,
                    "lag_2w_cases": value,
                    "rolling_4w_mean_cases": value,
                    "season_week_sin": 0.0,
                    "season_week_cos": 1.0,
                },
                target_value=value,
                target_outbreak_label=value >= 12,
                split_type="train",
            )
        )
    return rows


def test_keep_last_n_weeks_returns_recent_copy_without_mutating() -> None:
    rows = _rows(8)

    recent = keep_last_n_weeks(rows, 3)

    assert [row.iso_week for row in recent] == ["2025-W06", "2025-W07", "2025-W08"]
    assert [row.iso_week for row in rows[:3]] == ["2025-W01", "2025-W02", "2025-W03"]


def test_run_learning_curve_returns_scores_for_sufficient_windows() -> None:
    groups = select_feature_groups(get_default_feature_groups(), ["case_counts"])

    curve = run_learning_curve(
        _rows(20),
        selected_groups=groups,
        history_windows_weeks=[4, 8, 12],
        target_metric="mae",
        model_name="feature_rule",
        min_train_size=4,
    )

    assert [item["history_weeks"] for item in curve] == [4, 8, 12]
    assert curve[0]["score"] is None
    assert curve[1]["n_predictions"] == 4
    assert curve[1]["score"] == 0.0


def test_run_learning_curve_handles_small_datasets_without_crashing() -> None:
    groups = select_feature_groups(get_default_feature_groups(), ["case_counts"])

    curve = run_learning_curve(
        _rows(3),
        selected_groups=groups,
        history_windows_weeks=[12],
        target_metric="mae",
        model_name="feature_rule",
        min_train_size=4,
    )

    assert curve == [
        {
            "history_weeks": 12,
            "n_rows": 3,
            "n_predictions": 0,
            "metric": "mae",
            "score": None,
            "meets_threshold": None,
        }
    ]
