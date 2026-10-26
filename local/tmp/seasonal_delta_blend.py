"""Deterministic weekly seasonal-level-change blend for Fresh shadow evaluation.

The adapter is deliberately small and auditable.  It predicts each horizon
from (a) the same ISO-week observation in one or more prior years and (b) the
recent level change relative to the matching window one year earlier.  Missing
inputs remain missing; they are never replaced by zero or interpolated.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any

from epidemic_core.kg_r3c.execution import (
    INTERVAL_LEVEL,
    FittedModelArtifact,
    TrainingRow,
    make_artifact,
)

SEASONAL_DELTA_BLEND_ID = "seasonal_delta_blend.v1"
SEASONAL_DELTA_BLEND_INTERVAL_METHOD = "CAUSAL_EMPIRICAL_ABS_RESIDUAL"
NULL_MISSING_LAG = "NONE_MISSING_LAG"
_PERIOD = 52
_MAX_TAIL = 220


def _clip(value: float, lower: float, upper: float) -> float:
    return min(max(value, lower), upper)


def _quantile(sorted_values: list[float], p: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = p * (len(sorted_values) - 1)
    lower = int(math.floor(pos))
    fraction = pos - lower
    if lower + 1 >= len(sorted_values):
        return sorted_values[-1]
    return sorted_values[lower] * (1.0 - fraction) + sorted_values[lower + 1] * fraction


def _required_mean(values: list[float | None], start: int, end: int) -> float | None:
    if start < 0 or end > len(values) or start >= end:
        return None
    segment = values[start:end]
    if any(value is None for value in segment):
        return None
    return sum(float(value) for value in segment if value is not None) / len(segment)


def _point_prediction(
    values: list[float | None],
    origin: int,
    horizon: int,
    *,
    seasonal_years: int,
    recent_window: int,
    blend_weight: float,
) -> float | None:
    target = origin + horizon
    references: list[tuple[float, float]] = []
    for year in range(1, seasonal_years + 1):
        index = target - _PERIOD * year
        if 0 <= index < len(values) and values[index] is not None:
            references.append((float(values[index]), 1.0 / year))
    if not references:
        return None
    seasonal = sum(value * weight for value, weight in references) / sum(
        weight for _, weight in references
    )
    recent = _required_mean(values, origin + 1 - recent_window, origin + 1)
    previous_level = _required_mean(
        values,
        origin + 1 - _PERIOD - recent_window,
        origin + 1 - _PERIOD,
    )
    if recent is None or previous_level is None:
        return None
    delta = max(seasonal + recent - previous_level, 0.0)
    return max(blend_weight * delta + (1.0 - blend_weight) * recent, 0.0)


def fit_seasonal_delta_blend(
    rows: list[TrainingRow], parameters: dict[str, Any], seed: int, cutoff: date
) -> FittedModelArtifact:
    """Fit only causal residual calibration; the point rule has no learned weights."""
    seasonal_years = int(_clip(float(parameters.get("seasonal_years", 1)), 1.0, 3.0))
    allowed_windows = (4, 8, 13, 26)
    requested_window = int(round(float(parameters.get("recent_window", 4))))
    recent_window = min(allowed_windows, key=lambda item: (abs(item - requested_window), item))
    blend_weight = _clip(float(parameters.get("blend_weight", 0.4)), 0.0, 1.0)
    usable = [row for row in rows if row.period_start <= cutoff]
    values = [None if row.value is None else max(float(row.value), 0.0) for row in usable]
    residuals: list[float] = []
    first_origin = max(_PERIOD * seasonal_years, _PERIOD + recent_window) - 1
    for origin in range(first_origin, len(values) - 1):
        actual = values[origin + 1]
        predicted = _point_prediction(
            values,
            origin,
            1,
            seasonal_years=seasonal_years,
            recent_window=recent_window,
            blend_weight=blend_weight,
        )
        if actual is not None and predicted is not None:
            residuals.append(abs(float(actual) - predicted))
    residuals.sort()
    state = {
        "model": SEASONAL_DELTA_BLEND_ID,
        "frequency": "week",
        "period": _PERIOD,
        "seasonal_years": seasonal_years,
        "recent_window": recent_window,
        "blend_weight": blend_weight,
        "residual_abs_q95": _quantile(residuals, 0.95),
        "residual_calibration_n": len(residuals),
        "tail": values[-_MAX_TAIL:],
        "missing_semantics": "NONE_NO_ZERO_NO_INTERPOLATION",
    }
    return make_artifact(SEASONAL_DELTA_BLEND_ID, seed, state, usable)


def predict_seasonal_delta_blend(
    fitted: FittedModelArtifact, horizons: list[int], issue_period: date
) -> list[dict[str, Any]]:
    del issue_period
    state = fitted.state
    values: list[float | None] = list(state["tail"])
    origin = len(values) - 1
    results: list[dict[str, Any]] = []
    for horizon in horizons:
        yhat = _point_prediction(
            values,
            origin,
            int(horizon),
            seasonal_years=int(state["seasonal_years"]),
            recent_window=int(state["recent_window"]),
            blend_weight=float(state["blend_weight"]),
        )
        if yhat is None:
            results.append(
                {
                    "horizon": horizon,
                    "yhat": None,
                    "lower_bound": None,
                    "upper_bound": None,
                    "interval_level": INTERVAL_LEVEL,
                    "interval_method": NULL_MISSING_LAG,
                }
            )
            continue
        half_width = float(state["residual_abs_q95"]) * math.sqrt(int(horizon))
        results.append(
            {
                "horizon": horizon,
                "yhat": round(yhat, 6),
                "lower_bound": round(max(yhat - half_width, 0.0), 6),
                "upper_bound": round(yhat + half_width, 6),
                "interval_level": INTERVAL_LEVEL,
                "interval_method": SEASONAL_DELTA_BLEND_INTERVAL_METHOD,
            }
        )
    return results
