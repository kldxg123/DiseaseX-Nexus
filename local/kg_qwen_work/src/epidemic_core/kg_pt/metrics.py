"""Forecast skill metrics for the parameter-tuning loop.

Conventions:
  * pairs with None (or non-finite) predictions/actuals are skipped, never
    imputed or zero-filled;
  * the MASE denominator is computed ONLY from the passed training slice
    (seasonal-naive-12 by default, naive-1 fallback for short slices), never
    from the full series;
  * every emitted number is finite-checked; a non-finite result raises
    ValueError instead of propagating NaN/Inf into ledgers.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

Pair = tuple[float | None, float | None, float | None, float | None]  # (yhat, actual, lower, upper)


def _finite_or_none(value: float | None) -> float | None:
    if value is None:
        return None
    v = float(value)
    return v if math.isfinite(v) else None


def _check_finite(name: str, value: float | None) -> float | None:
    if value is not None and not math.isfinite(value):
        raise ValueError(f"NON_FINITE_METRIC:{name}")
    return value


def mase_denominator(train_values: Sequence[float | None], season_lag: int = 12) -> float | None:
    """In-sample seasonal-naive mean absolute difference over the OBSERVED
    values of the given training slice only.

    Falls back to naive-1 differences when fewer than season_lag+1 observed
    values exist. Raises ValueError when fewer than 2 observed values exist.
    Returns None for a degenerate (zero) denominator, e.g. a constant slice.
    """
    obs = [float(v) for v in train_values if v is not None and math.isfinite(float(v))]
    if len(obs) < 2:
        raise ValueError(f"MASE_DENOMINATOR_INFEASIBLE: need >=2 observed values, got {len(obs)}")
    if len(obs) >= season_lag + 1:
        diffs = [abs(obs[i] - obs[i - season_lag]) for i in range(season_lag, len(obs))]
    else:
        diffs = [abs(obs[i] - obs[i - 1]) for i in range(1, len(obs))]
    denom = sum(diffs) / len(diffs)
    return denom if denom > 0 else None


def cell_metrics(pairs: Iterable[Pair], mase_denom: float | None = None) -> dict[str, Any]:
    """Skill metrics over labeled cells (yhat, actual, lower, upper).

    Cells with None/non-finite yhat or actual are skipped. Coverage and width
    use only labeled cells that also carry both interval bounds.
    """
    labeled: list[tuple[float, float, float | None, float | None]] = []
    for yhat, actual, lo, hi in pairs:
        y = _finite_or_none(yhat)
        a = _finite_or_none(actual)
        if y is None or a is None:
            continue
        labeled.append((y, a, _finite_or_none(lo), _finite_or_none(hi)))
    n = len(labeled)
    if n == 0:
        return {
            "mae": None,
            "rmse": None,
            "wape": None,
            "smape": None,
            "mase": None,
            "coverage": None,
            "avg_width": None,
            "n": 0,
        }
    abs_e = [abs(y - a) for y, a, _, _ in labeled]
    sq_e = [(y - a) ** 2 for y, a, _, _ in labeled]
    mae = sum(abs_e) / n
    rmse = math.sqrt(sum(sq_e) / n)
    actual_mass = sum(abs(a) for _, a, _, _ in labeled)
    wape = sum(abs_e) / actual_mass if actual_mass > 0 else None
    smape_terms = [0.0 if (abs(y) + abs(a)) == 0 else 2.0 * abs(y - a) / (abs(y) + abs(a)) for y, a, _, _ in labeled]
    smape = sum(smape_terms) / n
    mase = mae / mase_denom if mase_denom else None
    bounded: list[tuple[float, float, float, float]] = []
    for y, a, lo, hi in labeled:
        if lo is None or hi is None:
            continue
        bounded.append((y, a, lo, hi))
    coverage = (sum(1 for _, a, lo, hi in bounded if lo <= a <= hi) / n) if bounded else None
    avg_width = (sum(hi - lo for _, _, lo, hi in bounded) / len(bounded)) if bounded else None
    out = {
        "mae": _check_finite("mae", mae),
        "rmse": _check_finite("rmse", rmse),
        "wape": _check_finite("wape", wape),
        "smape": _check_finite("smape", smape),
        "mase": _check_finite("mase", mase),
        "coverage": _check_finite("coverage", coverage),
        "avg_width": _check_finite("avg_width", avg_width),
        "n": n,
    }
    return out


def _labeled_key_map(preds: Iterable[Mapping[str, Any]]) -> dict[tuple[Any, Any], float]:
    """(origin, horizon) -> yhat for cells with finite yhat and actual."""
    out: dict[tuple[Any, Any], float] = {}
    for p in preds:
        y = _finite_or_none(p.get("yhat"))
        a = _finite_or_none(p.get("actual_value"))
        if y is None or a is None:
            continue
        out[(p.get("origin_index"), p.get("horizon"))] = y
    return out


def common_cells(preds_a: Iterable[Mapping[str, Any]], preds_b: Iterable[Mapping[str, Any]]) -> list[tuple[Any, Any]]:
    """Shared labeled (origin, horizon) cells of two prediction sets."""
    keys_a = set(_labeled_key_map(preds_a))
    keys_b = set(_labeled_key_map(preds_b))
    return sorted(keys_a & keys_b)


def wape_common(
    cells: Iterable[Any], pred_map: Mapping[Any, float | None], actual_map: Mapping[Any, float | None]
) -> float | None:
    """WAPE restricted to the given shared cells; None when no cells or the
    actual mass is zero."""
    errors = 0.0
    actuals = 0.0
    n = 0
    for key in cells:
        y = _finite_or_none(pred_map.get(key))
        a = _finite_or_none(actual_map.get(key))
        if y is None or a is None:
            continue
        errors += abs(y - a)
        actuals += abs(a)
        n += 1
    if n == 0 or actuals == 0:
        return None
    return _check_finite("wape_common", errors / actuals)


def agreement_pct(wape: float | None) -> float | None:
    """Readability transform of WAPE: max(0, 1 - wape) * 100.

    This is NOT a classification accuracy and implies nothing about future
    guaranteed accuracy.
    """
    if wape is None:
        return None
    return _check_finite("agreement_pct", max(0.0, 1.0 - float(wape)) * 100.0)
