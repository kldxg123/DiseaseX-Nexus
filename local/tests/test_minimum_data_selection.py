from __future__ import annotations

from datetime import date, timedelta

import pytest

from predictor_agent.dataset import FeatureRow
from predictor_agent.minimum_data_analyzer import (
    get_default_feature_groups,
    greedy_select_minimum_groups,
    group_cost_score,
    select_feature_groups,
)


def _rows(count: int = 18) -> list[FeatureRow]:
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
                    "data_quality_mean": 1.0,
                    "season_week_sin": 0.0,
                    "season_week_cos": 1.0,
                },
                target_value=value,
                target_outbreak_label=value >= 9,
                split_type="train",
            )
        )
    return rows


def test_group_cost_score_combines_cost_missingness_and_risk() -> None:
    groups = select_feature_groups(get_default_feature_groups(), ["case_counts", "data_quality"])

    assert group_cost_score(groups) > 0
    assert group_cost_score([]) == 0.0


def test_greedy_select_minimum_groups_finds_reaching_combination() -> None:
    groups = select_feature_groups(get_default_feature_groups(), ["data_quality", "case_counts"])

    result = greedy_select_minimum_groups(
        _rows(),
        candidate_groups=groups,
        target_metric="mae",
        min_acceptable_score=0.1,
        model_name="feature_rule",
        min_train_size=4,
        history_weeks=12,
    )

    assert result["reached_threshold"] is True
    assert result["minimum_feature_set"] == ["case_counts"]
    assert result["expected_score"] == pytest.approx(0.0)
    assert result["cost_summary"]["group_count"] == 1


def test_greedy_select_minimum_groups_returns_best_effort_when_threshold_unmet() -> None:
    groups = select_feature_groups(get_default_feature_groups(), ["case_counts", "data_quality"])

    result = greedy_select_minimum_groups(
        _rows(),
        candidate_groups=groups,
        target_metric="pr_auc",
        min_acceptable_score=1.1,
        model_name="feature_rule",
        min_train_size=4,
        history_weeks=12,
    )

    assert result["reached_threshold"] is False
    assert result["minimum_feature_set"]
    assert result["expected_score"] is not None
