# ruff: noqa: E501
"""Metric correctness, missing/NaN guards and MASE slice-locality (pure)."""

import math

import pytest

from epidemic_core.kg_pt.metrics import (
    agreement_pct,
    cell_metrics,
    common_cells,
    mase_denominator,
    wape_common,
)


def test_mase_denominator_seasonal_naive_12():
    values = [float(i) for i in range(24)]  # |y_t - y_{t-12}| == 12 for all t >= 12
    assert mase_denominator(values, season_lag=12) == 12.0


def test_mase_denominator_naive1_fallback_for_short_slices():
    values = [float(i) for i in range(10)]  # < 13 observed -> naive-1 diffs == 1
    assert mase_denominator(values, season_lag=12) == 1.0


def test_mase_denominator_skips_missing_never_imputes():
    values = [float(i) if i % 5 else None for i in range(24)]
    with_missing = mase_denominator(values)
    zero_filled = mase_denominator([0.0 if v is None else v for v in values])
    assert with_missing is not None and with_missing != zero_filled


def test_mase_denominator_raises_below_two_observed():
    with pytest.raises(ValueError, match="MASE_DENOMINATOR_INFEASIBLE"):
        mase_denominator([3.0])
    with pytest.raises(ValueError, match="MASE_DENOMINATOR_INFEASIBLE"):
        mase_denominator([None, None])


def test_mase_denominator_constant_slice_returns_none():
    assert mase_denominator([7.0] * 30) is None


def test_mase_denominator_uses_only_the_passed_slice():
    head = [float((i * i) % 17) for i in range(60)]
    tail = [float((i * i) % 29 + 3) for i in range(60)]
    assert mase_denominator(head) != mase_denominator(tail)
    # a full-series denominator must NOT leak into a slice-local one
    assert mase_denominator(head) != mase_denominator(head + tail)


def test_cell_metrics_known_values():
    pairs = [
        (12.0, 10.0, 5.0, 15.0),
        (8.0, 10.0, 6.0, 9.0),
    ]
    m = cell_metrics(pairs, mase_denom=4.0)
    assert m["n"] == 2
    assert m["mae"] == 2.0
    assert m["rmse"] == 2.0
    assert m["wape"] == 4.0 / 20.0
    assert m["smape"] == pytest.approx((2 * 2 / 22 + 2 * 2 / 18) / 2)
    assert m["mase"] == 0.5
    assert m["coverage"] == 0.5  # second actual (10) outside [6, 9]
    assert m["avg_width"] == 6.5  # (15-5 + 9-6) / 2


def test_cell_metrics_skips_none_and_non_finite():
    pairs = [
        (10.0, 10.0, 5.0, 15.0),
        (None, 10.0, 1.0, 2.0),
        (10.0, None, 1.0, 2.0),
        (float("nan"), 10.0, 1.0, 2.0),
        (10.0, float("inf"), 1.0, 2.0),
    ]
    m = cell_metrics(pairs, mase_denom=2.0)
    assert m["n"] == 1
    assert m["mae"] == 0.0
    for key in ("mae", "rmse", "wape", "smape", "mase", "coverage", "avg_width"):
        assert m[key] is None or math.isfinite(m[key])


def test_cell_metrics_empty():
    m = cell_metrics([], mase_denom=1.0)
    assert m["n"] == 0
    assert all(m[k] is None for k in ("mae", "rmse", "wape", "smape", "mase", "coverage", "avg_width"))


def test_wape_common_over_shared_cells_only():
    preds_a = [
        {"origin_index": 0, "horizon": 1, "yhat": 10.0, "actual_value": 10.0},
        {"origin_index": 0, "horizon": 2, "yhat": 20.0, "actual_value": 10.0},
    ]
    preds_b = [
        {"origin_index": 0, "horizon": 1, "yhat": 12.0, "actual_value": 10.0},
        {"origin_index": 0, "horizon": 2, "yhat": None, "actual_value": 10.0},  # not labeled for b
    ]
    cells = common_cells(preds_a, preds_b)
    assert cells == [(0, 1)]
    pred_map = {(p["origin_index"], p["horizon"]): p["yhat"] for p in preds_a}
    actual_map = {(p["origin_index"], p["horizon"]): p["actual_value"] for p in preds_a}
    assert wape_common(cells, pred_map, actual_map) == 0.0
    assert wape_common([], pred_map, actual_map) is None


def test_agreement_pct():
    assert agreement_pct(0.3) == pytest.approx(70.0)
    assert agreement_pct(1.5) == 0.0  # floored, never negative
    assert agreement_pct(None) is None
