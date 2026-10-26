from __future__ import annotations

from datetime import date, timedelta

import pytest

from predictor_agent.dataset import FeatureRow
from predictor_agent.minimum_data_analyzer import (
    get_default_feature_groups,
    run_feature_group_ablation,
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
                },
                target_value=value,
                target_outbreak_label=value >= 10,
                split_type="train",
            )
        )
    return rows


def test_run_feature_group_ablation_returns_delta_per_group() -> None:
    groups = select_feature_groups(get_default_feature_groups(), ["case_counts", "data_quality"])

    results = run_feature_group_ablation(
        _rows(),
        candidate_groups=groups,
        target_metric="mae",
        model_name="feature_rule",
        min_train_size=4,
        history_weeks=12,
    )

    assert [item["removed_group"] for item in results] == ["case_counts", "data_quality"]
    assert all("delta" in item for item in results)
    assert results[0]["baseline_score"] == pytest.approx(0.0)
    assert results[0]["importance_rank_hint"] == "non_removable"
    assert results[1]["importance_rank_hint"] == "removable"
