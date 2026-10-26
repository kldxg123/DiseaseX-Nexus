from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from predictor_agent.baseline import (
    MODEL_NAME,
    MODEL_VERSION,
    UNCERTAINTY_METHOD,
    BaselinePrediction,
    MovingAverageBaseline,
)


@dataclass(frozen=True)
class PredictionOutput:
    predicted_cases: float
    predicted_outbreak_probability: float
    lower_bound: float
    upper_bound: float
    explanation: dict


class SeasonalNaivePredictor:
    name = "seasonal_naive"
    model_name = "seasonal_naive"
    model_version = "v1"

    def __init__(
        self,
        *,
        seasonal_lag_weeks: int = 52,
        fallback_window_size: int = 4,
        fallback_window: int | None = None,
        outbreak_threshold: int = 3,
    ):
        if fallback_window is not None:
            fallback_window_size = fallback_window
        if seasonal_lag_weeks <= 0:
            raise ValueError("seasonal_lag_weeks must be positive.")
        if fallback_window_size <= 0:
            raise ValueError("fallback_window_size must be positive.")
        self.seasonal_lag_weeks = int(seasonal_lag_weeks)
        self.fallback_window_size = int(fallback_window_size)
        self.outbreak_threshold = int(outbreak_threshold)
        self._target_values: list[float] = []
        self._residual_std = 0.0

    def fit(self, target_values: list) -> "SeasonalNaivePredictor":
        self._target_values = [_target_value(value) for value in target_values if _target_value(value) is not None]
        self._residual_std = _seasonal_residual_std(
            self._target_values,
            self.seasonal_lag_weeks,
            self.fallback_window_size,
        )
        return self

    def predict(self) -> BaselinePrediction:
        if not self._target_values:
            raise ValueError("SeasonalNaivePredictor requires at least one training value.")
        if len(self._target_values) > self.seasonal_lag_weeks:
            predicted = self._target_values[-self.seasonal_lag_weeks]
        else:
            window = self._target_values[-self.fallback_window_size :]
            predicted = sum(window) / len(window)
        margin = 1.96 * self._residual_std
        lower = max(0.0, predicted - margin)
        upper = max(lower, predicted + margin)
        return BaselinePrediction(
            predicted_value=float(predicted),
            lower_bound=float(lower),
            upper_bound=float(upper),
            uncertainty_method=UNCERTAINTY_METHOD,
            residual_std=float(self._residual_std),
            training_count=len(self._target_values),
        )

    def predict_one(self, record) -> PredictionOutput:
        fallback_used = len(self._target_values) <= self.seasonal_lag_weeks
        return baseline_prediction_to_output(
            self.predict(),
            model_name=self.model_name,
            fallback_used=fallback_used,
            input_feature_keys=sorted((getattr(record, "feature_json", None) or {}).keys()),
            outbreak_threshold=self.outbreak_threshold,
        )


class FeatureRulePredictor:
    name = "feature_rule"
    model_name = "feature_rule"
    model_version = "v1"

    def __init__(self, *, window_size: int = 4, window: int | None = None, outbreak_threshold: int = 3):
        if window is not None:
            window_size = window
        if window_size <= 0:
            raise ValueError("window_size must be positive.")
        self.window_size = int(window_size)
        self.outbreak_threshold = int(outbreak_threshold)
        self._target_values: list[float] = []
        self._feature_json_values: list[dict] = []
        self._residual_std = 0.0

    def fit(self, rows: list[Any]) -> "FeatureRulePredictor":
        self._target_values = []
        self._feature_json_values = []
        for row in rows:
            if isinstance(row, (int, float)):
                self._target_values.append(float(row))
                self._feature_json_values.append({})
                continue
            self._target_values.append(float(getattr(row, "target_value", 0.0)))
            feature_json = getattr(row, "feature_json", None) or {}
            self._feature_json_values.append(dict(feature_json))
        self._residual_std = _feature_rule_residual_std(
            self._target_values,
            self._feature_json_values,
            self.window_size,
        )
        return self

    def predict(self, feature_json: dict | None = None) -> BaselinePrediction:
        if not self._target_values:
            raise ValueError("FeatureRulePredictor requires at least one training row.")
        features = dict(feature_json or (self._feature_json_values[-1] if self._feature_json_values else {}))
        predicted = _feature_rule_prediction(features)
        if predicted is None:
            window = self._target_values[-self.window_size :]
            predicted = sum(window) / len(window)
        predicted = max(0.0, float(predicted))
        margin = 1.96 * self._residual_std
        lower = max(0.0, predicted - margin)
        upper = max(lower, predicted + margin)
        return BaselinePrediction(
            predicted_value=predicted,
            lower_bound=float(lower),
            upper_bound=float(upper),
            uncertainty_method=UNCERTAINTY_METHOD,
            residual_std=float(self._residual_std),
            training_count=len(self._target_values),
        )

    def predict_one(self, record) -> PredictionOutput:
        feature_json = getattr(record, "feature_json", None) or {}
        return baseline_prediction_to_output(
            self.predict(feature_json),
            model_name=self.model_name,
            fallback_used=_feature_rule_prediction(feature_json) is None,
            input_feature_keys=sorted(feature_json.keys()),
            outbreak_threshold=self.outbreak_threshold,
        )


def get_predictor_factory(name: str) -> Callable[..., Any]:
    normalized = (name or "").strip().lower()
    aliases: dict[str, Callable[..., Any]] = {
        "moving_average": MovingAverageBaseline,
        "moving_average_baseline": MovingAverageBaseline,
        MODEL_NAME: MovingAverageBaseline,
        "seasonal_naive": SeasonalNaivePredictor,
        "feature_rule": FeatureRulePredictor,
    }
    try:
        return aliases[normalized]
    except KeyError as exc:
        raise ValueError(f"Unknown predictor: {name}.") from exc


SeasonalNaiveBaseline = SeasonalNaivePredictor
FeatureRuleBaseline = FeatureRulePredictor


def get_baseline_model(name: str, **kwargs):
    factory = get_predictor_factory(name)
    try:
        return factory(**kwargs)
    except TypeError:
        translated = dict(kwargs)
        if "window" in translated and "window_size" not in translated:
            translated["window_size"] = translated.pop("window")
        if "fallback_window" in translated and "fallback_window_size" not in translated:
            translated["fallback_window_size"] = translated.pop("fallback_window")
        return factory(**translated)


def baseline_prediction_to_output(
    prediction: BaselinePrediction,
    *,
    model_name: str,
    fallback_used: bool,
    input_feature_keys: list[str],
    outbreak_threshold: int,
) -> PredictionOutput:
    predicted_cases = max(0.0, float(prediction.predicted_value))
    probability = _probability_from_cases(predicted_cases, outbreak_threshold)
    return PredictionOutput(
        predicted_cases=predicted_cases,
        predicted_outbreak_probability=probability,
        lower_bound=max(0.0, float(prediction.lower_bound)),
        upper_bound=max(predicted_cases, float(prediction.upper_bound)),
        explanation={
            "model_name": model_name,
            "fallback_used": bool(fallback_used),
            "input_feature_keys": list(input_feature_keys),
            "training_rows": int(prediction.training_count),
            "uncertainty_method": prediction.uncertainty_method,
            "residual_std": float(prediction.residual_std),
        },
    )


def _seasonal_residual_std(
    values: list[float],
    seasonal_lag_weeks: int,
    fallback_window_size: int,
) -> float:
    if len(values) < 2:
        return 0.0
    residuals: list[float] = []
    for index in range(1, len(values)):
        if index >= seasonal_lag_weeks:
            predicted = values[index - seasonal_lag_weeks]
        else:
            history = values[max(0, index - fallback_window_size) : index]
            predicted = sum(history) / len(history)
        residuals.append(values[index] - predicted)
    return _sample_std(residuals)


def _feature_rule_residual_std(
    target_values: list[float],
    feature_json_values: list[dict],
    window_size: int,
) -> float:
    residuals: list[float] = []
    for index, actual in enumerate(target_values):
        predicted = _feature_rule_prediction(feature_json_values[index])
        if predicted is None and index > 0:
            history = target_values[max(0, index - window_size) : index]
            predicted = sum(history) / len(history)
        if predicted is not None:
            residuals.append(actual - float(predicted))
    return _sample_std(residuals)


def _feature_rule_prediction(feature_json: dict) -> float | None:
    weighted_values: list[tuple[float, float]] = []
    for key, weight in (
        ("current_week_cases", 0.4),
        ("rolling_4w_mean_cases", 0.3),
        ("lag_1w_cases", 0.2),
        ("lag_2w_cases", 0.1),
    ):
        value = _as_float(feature_json.get(key))
        if value is not None:
            weighted_values.append((value, weight))
    if not weighted_values:
        return None
    total_weight = sum(weight for _value, weight in weighted_values)
    return sum(value * weight for value, weight in weighted_values) / total_weight


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _sample_std(values: list[float]) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return abs(values[0])
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    return math.sqrt(max(0.0, variance))


def _target_value(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    target = getattr(value, "target_value", None)
    if target is None:
        return None
    return float(target)


def _probability_from_cases(predicted_cases: float, outbreak_threshold: int) -> float:
    threshold = max(1.0, float(outbreak_threshold))
    return max(0.0, min(1.0, predicted_cases / (threshold * 2.0)))


__all__ = [
    "BaselinePrediction",
    "FeatureRuleBaseline",
    "FeatureRulePredictor",
    "MODEL_NAME",
    "MODEL_VERSION",
    "MovingAverageBaseline",
    "PredictionOutput",
    "SeasonalNaiveBaseline",
    "SeasonalNaivePredictor",
    "UNCERTAINTY_METHOD",
    "baseline_prediction_to_output",
    "get_baseline_model",
    "get_predictor_factory",
]
