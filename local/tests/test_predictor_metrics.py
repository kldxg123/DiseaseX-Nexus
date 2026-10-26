from __future__ import annotations

import json

import pytest

from predictor_agent.metrics import (
    auroc_score,
    average_precision_score,
    auroc,
    brier_score,
    classification_metrics,
    f1_score,
    f1_score_binary,
    pr_auc,
    regression_metrics,
    smape,
    summarize_classification_metrics,
    summarize_regression_metrics,
)


def test_regression_metrics_include_smape_and_json_safe_summary() -> None:
    actual = [100.0, 200.0]
    predicted = [110.0, 190.0]

    assert round(smape(actual, predicted), 6) == 0.07326
    summary = summarize_regression_metrics(actual, predicted, [90.0, 180.0], [120.0, 220.0])

    assert summary["n"] == 2
    assert summary["mae"] == 10.0
    assert summary["interval_coverage"] == 1.0
    json.dumps(summary)


def test_classification_metrics_for_perfect_ranking() -> None:
    labels = [1, 0, 1, 0]
    probabilities = [0.9, 0.1, 0.8, 0.2]

    assert brier_score(labels, probabilities) == pytest.approx(0.025)
    assert f1_score(labels, probabilities) == 1.0
    assert auroc(labels, probabilities) == 1.0
    assert pr_auc(labels, probabilities) == 1.0
    summary = summarize_classification_metrics(labels, probabilities)

    assert summary["n"] == 4
    assert summary["threshold"] == 0.5
    json.dumps(summary)


def test_classification_metrics_degenerate_inputs_are_safe() -> None:
    assert brier_score([], []) == 0.0
    assert f1_score([], []) == 0.0
    assert auroc([1, 1], [0.9, 0.8]) == 0.0
    assert pr_auc([0, 0], [0.2, 0.1]) == 0.0


def test_stable_metric_aliases_return_safe_values() -> None:
    labels = [True, False, True, False]
    probabilities = [0.9, 0.1, 0.8, 0.2]

    assert f1_score_binary(labels, [True, False, True, False]) == 1.0
    assert auroc_score(labels, probabilities) == 1.0
    assert average_precision_score(labels, probabilities) == 1.0
    assert auroc_score([True, True], [0.8, 0.7]) is None
    assert average_precision_score([False, False], [0.2, 0.1]) is None

    classification = classification_metrics(labels, probabilities)
    regression = regression_metrics([1.0, 2.0], [1.5, 1.5])

    assert classification["n"] == 4
    assert classification["pr_auc"] == 1.0
    assert regression["n"] == 2
    json.dumps({"classification": classification, "regression": regression})
