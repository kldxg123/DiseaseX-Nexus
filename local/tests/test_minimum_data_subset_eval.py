from __future__ import annotations

from datetime import date, timedelta

from predictor_agent.dataset import FeatureRow
from predictor_agent.minimum_data_analyzer import (
    FeatureSubsetEvaluation,
    choose_best_minimum_set,
    evaluate_feature_subset,
    generate_candidate_feature_group_sets,
    get_default_feature_groups,
)


def _rows(count: int = 14) -> list[FeatureRow]:
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
                horizon_days=28,
                feature_json={
                    "current_week_cases": value,
                    "lag_1w_cases": value,
                    "lag_2w_cases": value,
                    "lag_4w_cases": value,
                    "rolling_4w_mean_cases": value,
                    "data_quality_mean": 1.0,
                    "season_week_sin": 0.0,
                    "season_week_cos": 1.0,
                },
                target_value=value,
                target_outbreak_label=value >= 8,
                split_type="train",
            )
        )
    return rows


def test_evaluate_feature_subset_returns_structured_result() -> None:
    evaluation = evaluate_feature_subset(
        _rows(),
        feature_groups=["case_history"],
        history_weeks=12,
        specs=get_default_feature_groups(),
        target_metric="mae",
        min_acceptable_score=0.1,
        horizon_days=28,
        min_train_size=4,
    )

    assert isinstance(evaluation, FeatureSubsetEvaluation)
    assert evaluation.feature_groups == ("case_history",)
    assert evaluation.score == 0.0
    assert evaluation.passed is True
    assert evaluation.records_used == 12


def test_evaluate_feature_subset_handles_insufficient_data() -> None:
    evaluation = evaluate_feature_subset(
        _rows(3),
        feature_groups=["case_history"],
        history_weeks=12,
        specs=get_default_feature_groups(),
        target_metric="mae",
        min_acceptable_score=0.1,
        horizon_days=28,
        min_train_size=4,
    )

    assert evaluation.score is None
    assert evaluation.passed is False
    assert evaluation.warnings


def test_candidate_generation_and_best_minimum_set_selection() -> None:
    candidates = generate_candidate_feature_group_sets(["quality", "case_history", "seasonality"])
    evaluations = [
        FeatureSubsetEvaluation(("quality",), 12, 1.0, "regression.mae", "lower", True, 0.5, 1, 12, []),
        FeatureSubsetEvaluation(("case_history",), 8, 0.0, "regression.mae", "lower", True, 0.35, 1, 8, []),
        FeatureSubsetEvaluation(("case_history", "quality"), 8, 0.0, "regression.mae", "lower", True, 0.55, 2, 8, []),
        FeatureSubsetEvaluation(("seasonality",), 8, None, "regression.mae", "lower", False, 0.1, 1, 8, []),
    ]

    best = choose_best_minimum_set(evaluations)

    assert ("quality",) in candidates
    assert ("quality", "case_history") in candidates
    assert best is not None
    assert best.feature_groups == ("case_history",)
    assert choose_best_minimum_set([evaluations[-1]]) is None
