"""Time-ordered nested evaluation with disjoint selection/calibration targets."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence
import numpy as np

from .chronological_validation import MODELS, forecast, quantile_forecast


@dataclass(frozen=True)
class Fold:
    test_start: int
    test_end: int
    selection_start: int
    selection_end: int
    calibration_start: int
    calibration_end: int


def make_fold(start: int, end: int, window: int = 52) -> Fold:
    if end <= start or start - 2 * window < 104:
        raise ValueError('insufficient history or invalid outer test')
    return Fold(start, end, start-2*window, start-window, start-window, start)


def historical_predictions(values: Sequence[float], start: int, end: int, model: str):
    """Targets never cross the exclusive end of a historical partition."""
    y = np.asarray(values, dtype=float)
    if end > len(y) or start < 104 or end <= start:
        raise ValueError('invalid historical partition')
    records = []
    for origin in range(start, end):
        points = forecast(y[:origin], model, 4)
        for h, point in enumerate(points, 1):
            target = origin+h-1
            if target < end:
                records.append((origin, h, target, float(y[target]), float(point)))
    return records


def select_and_calibrate(history: Sequence[float], fold: Fold):
    """Accept only the prefix ending before outer-test labels."""
    if len(history) != fold.test_start:
        raise ValueError('outer test values must not enter selection/calibration')
    losses = {}
    residuals = {}
    for model in MODELS:
        selection = historical_predictions(history, fold.selection_start, fold.selection_end, model)
        losses[model] = sum(abs(y-p) for _, _, _, y, p in selection)
        calibration = historical_predictions(history, fold.calibration_start, fold.calibration_end, model)
        residuals[model] = {h: [y-p for _, hh, _, y, p in calibration if hh == h] for h in range(1,5)}
        if min(map(len, residuals[model].values())) < 26:
            raise ValueError('insufficient calibration support')
    chosen = min(MODELS, key=lambda m: losses[m])
    return chosen, losses, residuals
