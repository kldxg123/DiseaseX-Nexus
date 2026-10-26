from __future__ import annotations

import math
from dataclasses import dataclass


MODEL_NAME = "moving_average_baseline"
MODEL_VERSION = "v1"
UNCERTAINTY_METHOD = "rolling_residual_std_95pct"


@dataclass(frozen=True)
class BaselinePrediction:
    predicted_value: float
    lower_bound: float
    upper_bound: float
    uncertainty_method: str
    residual_std: float
    training_count: int


class MovingAverageBaseline:
    name = "moving_average"

    def __init__(self, *, window_size: int = 4, window: int | None = None, outbreak_threshold: int = 3):
        if window is not None:
            window_size = window
        if window_size <= 0:
            raise ValueError("window_size must be positive.")
        self.window_size = int(window_size)
        self.outbreak_threshold = int(outbreak_threshold)
        self._target_values: list[float] = []
        self._residual_std = 0.0

    def fit(self, target_values: list) -> "MovingAverageBaseline":
        self._target_values = [_target_value(value) for value in target_values if _target_value(value) is not None]
        self._residual_std = _residual_std(self._target_values, self.window_size)
        return self

    def predict(self) -> BaselinePrediction:
        if not self._target_values:
            raise ValueError("MovingAverageBaseline requires at least one training value.")
        window = self._target_values[-self.window_size :]
        predicted = sum(window) / len(window)
        residual_std = self._residual_std
        margin = 1.96 * residual_std
        lower = max(0.0, predicted - margin)
        upper = max(lower, predicted + margin)
        return BaselinePrediction(
            predicted_value=float(predicted),
            lower_bound=float(lower),
            upper_bound=float(upper),
            uncertainty_method=UNCERTAINTY_METHOD,
            residual_std=float(residual_std),
            training_count=len(self._target_values),
        )

    def predict_one(self, record) -> object:
        from predictor_agent.baselines import baseline_prediction_to_output

        return baseline_prediction_to_output(
            self.predict(),
            model_name=self.name,
            fallback_used=False,
            input_feature_keys=sorted((getattr(record, "feature_json", None) or {}).keys()),
            outbreak_threshold=self.outbreak_threshold,
        )


def _residual_std(values: list[float], window_size: int) -> float:
    if len(values) < 2:
        return 0.0
    residuals: list[float] = []
    for index in range(1, len(values)):
        history = values[max(0, index - window_size) : index]
        predicted = sum(history) / len(history)
        residuals.append(values[index] - predicted)
    if len(residuals) < 2:
        return abs(residuals[0]) if residuals else 0.0
    mean = sum(residuals) / len(residuals)
    variance = sum((value - mean) ** 2 for value in residuals) / (len(residuals) - 1)
    return math.sqrt(max(0.0, variance))


def _target_value(value) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    target = getattr(value, "target_value", None)
    if target is None:
        return None
    return float(target)
