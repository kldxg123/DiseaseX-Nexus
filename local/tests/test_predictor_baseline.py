from __future__ import annotations

from predictor_agent.baseline import MovingAverageBaseline
from predictor_agent.metrics import (
    interval_coverage,
    mean_absolute_error,
    mean_absolute_percentage_error,
    root_mean_squared_error,
)


def test_moving_average_baseline_predicts_with_uncertainty_interval() -> None:
    prediction = MovingAverageBaseline(window_size=3).fit([1, 2, 3, 4, 5]).predict()

    assert prediction.predicted_value == 4.0
    assert prediction.lower_bound <= prediction.predicted_value <= prediction.upper_bound
    assert prediction.uncertainty_method == "rolling_residual_std_95pct"
    assert prediction.training_count == 5


def test_regression_metric_helpers() -> None:
    actual = [2.0, 4.0, 6.0]
    predicted = [1.0, 5.0, 6.0]

    assert mean_absolute_error(actual, predicted) == 2 / 3
    assert root_mean_squared_error(actual, predicted) > 0
    assert mean_absolute_percentage_error(actual, predicted) > 0
    assert interval_coverage(actual, [0.0, 3.0, 5.5], [2.5, 4.5, 6.5]) == 1.0
