from __future__ import annotations

from predictor_agent.minimum_data_analyzer import (
    extract_backtest_score,
    normalize_target_metric,
    score_passes_threshold,
)


def test_normalize_target_metric_aliases_and_directions() -> None:
    assert normalize_target_metric("mae") == ("regression.mae", "lower")
    assert normalize_target_metric("rmse") == ("regression.rmse", "lower")
    assert normalize_target_metric("brier") == ("classification.brier_score", "lower")
    assert normalize_target_metric("f1") == ("classification.f1_score", "higher")
    assert normalize_target_metric("pr_auc") == ("classification.average_precision", "higher")
    assert normalize_target_metric("average_precision") == ("classification.average_precision", "higher")


def test_extract_backtest_score_supports_run_backtest_shape_and_missing_metrics() -> None:
    result = {
        "metrics": {
            "regression": {"mae": 1.5, "rmse": 2.0},
            "classification": {"brier_score": 0.1, "average_precision": 0.8},
        }
    }

    assert extract_backtest_score(result, "mae") == 1.5
    assert extract_backtest_score(result, "classification.average_precision") == 0.8
    assert extract_backtest_score({"metrics": {"regression": {}}}, "mae") is None


def test_score_passes_threshold_handles_lower_and_higher_metrics() -> None:
    assert score_passes_threshold(0.5, 1.0, "lower") is True
    assert score_passes_threshold(1.5, 1.0, "lower") is False
    assert score_passes_threshold(0.8, 0.7, "higher") is True
    assert score_passes_threshold(0.6, 0.7, "higher") is False
    assert score_passes_threshold(None, 0.7, "higher") is False
