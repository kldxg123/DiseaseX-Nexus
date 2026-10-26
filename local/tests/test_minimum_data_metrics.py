from __future__ import annotations

import pytest

from predictor_agent.minimum_data_analyzer import extract_metric_score, metric_is_higher_better


def test_metric_direction_is_stable() -> None:
    for metric in ("pr_auc", "auroc", "f1_score", "interval_coverage"):
        assert metric_is_higher_better(metric) is True
    for metric in ("mae", "rmse", "mape", "smape", "brier_score"):
        assert metric_is_higher_better(metric) is False


def test_extract_metric_score_reads_regression_and_classification_metrics() -> None:
    payload = {
        "summary": {"overall": {"mae": 1.25, "rmse": 2.0}},
        "classification_metrics": {"pr_auc": 0.8, "brier_score": 0.2},
    }

    assert extract_metric_score(payload, "mae") == 1.25
    assert extract_metric_score(payload, "pr_auc") == 0.8
    assert extract_metric_score(payload, "brier_score") == 0.2
    assert extract_metric_score({"overall": {"mae": None}}, "mae") is None


def test_unknown_metric_is_rejected_clearly() -> None:
    with pytest.raises(ValueError, match="Unsupported target_metric"):
        metric_is_higher_better("not_a_metric")
    with pytest.raises(ValueError, match="Unsupported target_metric"):
        extract_metric_score({}, "not_a_metric")
