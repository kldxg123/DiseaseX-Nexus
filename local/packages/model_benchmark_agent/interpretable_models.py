from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from predictor_agent.datasets import FeatureRecord

from model_benchmark_agent.feature_groups import feature_keys_for_groups


@dataclass(frozen=True)
class InterpretablePrediction:
    prediction: float
    probability_score: float
    lower_bound: float
    upper_bound: float
    explanation_summary: dict[str, Any]
    used_feature_groups: list[str]
    warnings: list[str] = field(default_factory=list)


class BaseInterpretableModel:
    model_name = "base_interpretable"

    def __init__(self, *, feature_groups: list[str] | None = None):
        self.feature_groups = list(feature_groups or [])
        self.train_records: list[FeatureRecord] = []
        self.residual_std = 0.0
        self.warnings: list[str] = []

    def fit(self, records: list[FeatureRecord]) -> "BaseInterpretableModel":
        self.train_records = [record for record in records if record.target_value is not None]
        self.residual_std = _rolling_residual_std(self.train_records)
        return self

    def predict_one(self, record: FeatureRecord) -> InterpretablePrediction:
        raise NotImplementedError

    def _fallback_prediction(self) -> float:
        values = [float(record.target_value or 0.0) for record in self.train_records[-4:]]
        return sum(values) / len(values) if values else 0.0


class FeatureRuleBaseline(BaseInterpretableModel):
    model_name = "feature_rule_baseline"

    def predict_one(self, record: FeatureRecord) -> InterpretablePrediction:
        features = dict(record.feature_json or {})
        weighted = _weighted_average(
            features,
            {
                "current_week_cases": 0.26,
                "rolling_4w_mean_cases": 0.24,
                "lag_1w_cases": 0.18,
                "lag_2w_cases": 0.10,
                "positivity_rate": 18.0,
                "source_reliability_score": 3.0,
                "quality_score": 2.0,
            },
        )
        warnings = list(self.warnings)
        if weighted is None:
            predicted = self._fallback_prediction()
            warnings.append("missing_feature_group")
            warnings.append("fallback_moving_average")
        else:
            predicted = weighted
        return _prediction_output(
            model_name=self.model_name,
            predicted=predicted,
            residual_std=self.residual_std,
            feature_groups=self.feature_groups,
            features=features,
            warnings=warnings,
        )


class WeightedSignalBaseline(BaseInterpretableModel):
    model_name = "weighted_signal_baseline"

    DEFAULT_GROUP_WEIGHTS = {
        "epidemiological_lag": 0.34,
        "rolling_statistics": 0.22,
        "seasonality": 0.06,
        "testing": 0.10,
        "positivity": 0.14,
        "source_signal": 0.07,
        "data_quality": 0.07,
    }

    def __init__(
        self,
        *,
        feature_groups: list[str] | None = None,
        group_weights: dict[str, float] | None = None,
    ):
        super().__init__(feature_groups=feature_groups)
        self.group_weights = dict(group_weights or self.DEFAULT_GROUP_WEIGHTS)

    def predict_one(self, record: FeatureRecord) -> InterpretablePrediction:
        features = dict(record.feature_json or {})
        group_scores: list[tuple[float, float]] = []
        warnings = list(self.warnings)
        for group in self.feature_groups:
            score = _group_signal(group, features, self._fallback_prediction())
            if score is None:
                warnings.append(f"missing_feature_group:{group}")
                continue
            group_scores.append((score, self.group_weights.get(group, 0.05)))
        if not group_scores:
            predicted = self._fallback_prediction()
            warnings.append("fallback_moving_average")
        else:
            total_weight = sum(weight for _score, weight in group_scores)
            predicted = sum(score * weight for score, weight in group_scores) / total_weight
        return _prediction_output(
            model_name=self.model_name,
            predicted=predicted,
            residual_std=self.residual_std,
            feature_groups=self.feature_groups,
            features=features,
            warnings=warnings,
        )


class LagRollingLinearBaseline(BaseInterpretableModel):
    model_name = "lag_rolling_linear_baseline"

    def __init__(self, *, feature_groups: list[str] | None = None):
        super().__init__(feature_groups=feature_groups)
        self.coefficients: list[float] | None = None

    def fit(self, records: list[FeatureRecord]) -> "LagRollingLinearBaseline":
        super().fit(records)
        x_rows: list[list[float]] = []
        y_values: list[float] = []
        for record in self.train_records:
            features = record.feature_json or {}
            positivity = _num(features.get("positivity_rate"))
            row = [
                1.0,
                _num(features.get("lag_1w_cases")),
                _num(features.get("rolling_4w_mean_cases")),
                (positivity or 0.0) * 10.0,
            ]
            if row[1] is None or row[2] is None:
                continue
            x_rows.append([float(value or 0.0) for value in row])
            y_values.append(float(record.target_value or 0.0))
        if len(x_rows) < 4:
            self.warnings.append("insufficient_data")
            self.coefficients = None
            return self
        self.coefficients = _ordinary_least_squares(x_rows, y_values)
        if self.coefficients is None:
            self.warnings.append("linear_solve_failed")
            self.warnings.append("fallback_moving_average")
        return self

    def predict_one(self, record: FeatureRecord) -> InterpretablePrediction:
        features = dict(record.feature_json or {})
        warnings = list(self.warnings)
        positivity = _num(features.get("positivity_rate"))
        row = [
            1.0,
            _num(features.get("lag_1w_cases")),
            _num(features.get("rolling_4w_mean_cases")),
            (positivity or 0.0) * 10.0,
        ]
        if self.coefficients is None or row[1] is None or row[2] is None:
            predicted = self._fallback_prediction()
            warnings.append("fallback_moving_average")
        else:
            predicted = sum(float(value or 0.0) * coef for value, coef in zip(row, self.coefficients))
        return _prediction_output(
            model_name=self.model_name,
            predicted=predicted,
            residual_std=self.residual_std,
            feature_groups=self.feature_groups,
            features=features,
            warnings=warnings,
        )


class PositivityAdjustedBaseline(BaseInterpretableModel):
    model_name = "positivity_adjusted_baseline"

    def predict_one(self, record: FeatureRecord) -> InterpretablePrediction:
        features = dict(record.feature_json or {})
        warnings = list(self.warnings)
        base = _first_number(features, ["rolling_4w_mean_cases", "lag_1w_cases", "current_week_cases"])
        if base is None:
            base = self._fallback_prediction()
            warnings.append("fallback_moving_average")
        positivity = _num(features.get("positivity_rate"))
        tests = _num(features.get("tests_performed")) or _num(features.get("testing_volume"))
        if positivity is None:
            warnings.append("missing_feature_group:positivity")
            adjusted = base
        else:
            test_factor = 1.0
            if tests is not None:
                test_factor = min(1.25, max(0.85, tests / 160.0))
            adjusted = base * (1.0 + min(0.5, positivity)) * test_factor
        return _prediction_output(
            model_name=self.model_name,
            predicted=adjusted,
            residual_std=self.residual_std,
            feature_groups=self.feature_groups,
            features=features,
            warnings=warnings,
        )


MODEL_FACTORIES = {
    "feature_rule_baseline": FeatureRuleBaseline,
    "weighted_signal_baseline": WeightedSignalBaseline,
    "lag_rolling_linear_baseline": LagRollingLinearBaseline,
    "positivity_adjusted_baseline": PositivityAdjustedBaseline,
}


def get_interpretable_model(name: str, *, feature_groups: list[str] | None = None) -> BaseInterpretableModel:
    normalized = (name or "").strip().lower().replace("-", "_")
    try:
        return MODEL_FACTORIES[normalized](feature_groups=feature_groups)
    except KeyError as exc:
        raise ValueError(f"Unknown interpretable model: {name}") from exc


def _prediction_output(
    *,
    model_name: str,
    predicted: float,
    residual_std: float,
    feature_groups: list[str],
    features: dict[str, Any],
    warnings: list[str],
) -> InterpretablePrediction:
    predicted = max(0.0, float(predicted))
    margin = 1.96 * max(0.0, residual_std)
    used_keys = sorted(key for key in feature_keys_for_groups(feature_groups) if key in features)
    return InterpretablePrediction(
        prediction=predicted,
        probability_score=max(0.0, min(1.0, predicted / 30.0)),
        lower_bound=max(0.0, predicted - margin),
        upper_bound=max(predicted, predicted + margin),
        explanation_summary={
            "model_name": model_name,
            "used_feature_groups": list(feature_groups),
            "used_feature_key_count": len(used_keys),
            "fallback_used": any("fallback" in warning for warning in warnings),
        },
        used_feature_groups=list(feature_groups),
        warnings=sorted(set(warnings)),
    )


def _weighted_average(features: dict[str, Any], weights: dict[str, float]) -> float | None:
    values: list[tuple[float, float]] = []
    for key, weight in weights.items():
        value = _num(features.get(key))
        if value is not None:
            values.append((value, weight))
    if not values:
        return None
    total_weight = sum(abs(weight) for _value, weight in values)
    return sum(value * weight for value, weight in values) / total_weight


def _group_signal(group: str, features: dict[str, Any], fallback: float) -> float | None:
    if group == "epidemiological_lag":
        return _first_number(features, ["current_week_cases", "lag_1w_cases", "lag_2w_cases"])
    if group == "rolling_statistics":
        return _first_number(features, ["rolling_4w_mean_cases", "rolling_8w_mean_cases"])
    if group == "seasonality":
        sin_value = _num(features.get("season_sin"))
        return None if sin_value is None else fallback * (1.0 + 0.05 * sin_value)
    if group == "testing":
        tests = _num(features.get("tests_performed")) or _num(features.get("testing_volume"))
        return None if tests is None else fallback * min(1.2, max(0.8, tests / 160.0))
    if group == "positivity":
        positivity = _num(features.get("positivity_rate"))
        return None if positivity is None else fallback * (1.0 + min(0.5, positivity))
    if group == "source_signal":
        count = _num(features.get("source_count")) or _num(features.get("official_source_count"))
        return None if count is None else fallback * (1.0 + min(0.2, count / 50.0))
    if group == "data_quality":
        quality = _num(features.get("quality_score")) or _num(features.get("source_reliability_score"))
        return None if quality is None else fallback * max(0.5, min(1.1, quality))
    return None


def _first_number(features: dict[str, Any], keys: list[str]) -> float | None:
    for key in keys:
        value = _num(features.get(key))
        if value is not None:
            return value
    return None


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _rolling_residual_std(records: list[FeatureRecord]) -> float:
    if len(records) < 2:
        return 0.0
    residuals: list[float] = []
    for index in range(1, len(records)):
        history = [float(record.target_value or 0.0) for record in records[max(0, index - 4) : index]]
        prediction = sum(history) / len(history)
        residuals.append(float(records[index].target_value or 0.0) - prediction)
    return _std(residuals)


def _std(values: list[float]) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return abs(values[0])
    mean = sum(values) / len(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / (len(values) - 1))


def _ordinary_least_squares(x_rows: list[list[float]], y_values: list[float]) -> list[float] | None:
    size = len(x_rows[0])
    xtx = [[0.0 for _ in range(size)] for _ in range(size)]
    xty = [0.0 for _ in range(size)]
    for row, target in zip(x_rows, y_values):
        for i in range(size):
            xty[i] += row[i] * target
            for j in range(size):
                xtx[i][j] += row[i] * row[j]
    for i in range(size):
        xtx[i][i] += 1e-6
    return _solve_linear_system(xtx, xty)


def _solve_linear_system(matrix: list[list[float]], values: list[float]) -> list[float] | None:
    n = len(values)
    augmented = [row[:] + [values[i]] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda row: abs(augmented[row][col]))
        if abs(augmented[pivot][col]) < 1e-10:
            return None
        augmented[col], augmented[pivot] = augmented[pivot], augmented[col]
        divisor = augmented[col][col]
        augmented[col] = [value / divisor for value in augmented[col]]
        for row in range(n):
            if row == col:
                continue
            factor = augmented[row][col]
            augmented[row] = [
                current - factor * pivot_value
                for current, pivot_value in zip(augmented[row], augmented[col])
            ]
    return [augmented[row][-1] for row in range(n)]


__all__ = [
    "BaseInterpretableModel",
    "FeatureRuleBaseline",
    "InterpretablePrediction",
    "LagRollingLinearBaseline",
    "PositivityAdjustedBaseline",
    "WeightedSignalBaseline",
    "get_interpretable_model",
]
