"""Unit tests for the execution contract adapters (pure fixtures, no DB)."""
from datetime import date

import pytest

from epidemic_core.kg_r3c.execution import (
    SEASONAL_NAIVE_NOT_ELIGIBLE,
    TrainingRow,
    fit_model,
    nb_quantiles,
    predict_model,
)


def _rows(values, start=date(2020, 1, 1), frequency="month"):
    from epidemic_core.forecast_model_kg.dual_loop import period_add

    rows = []
    for i, v in enumerate(values):
        p = period_add(start, frequency, i)
        rows.append(TrainingRow(period_start=p, period_end=p, value=v,
                                observation_id=f"obs-{i}", revision=1))
    return rows


VALUES = [3, 4, 2, 5, 6, 4, 3, 7, 8, 6, 5, 9, 10, 8, 7, 11, 12, 10, 9, 13,
          14, 12, 11, 15, 16, 14, 13, 17, 18, 16, 15, 19, 20, 18, 17, 21]
H = [1, 2, 3, 4]
CUTOFF = date(2022, 12, 1)


@pytest.mark.parametrize("adapter", ["negbin_dglm.v1", "hurdle_count_ss.v1"])
def test_fit_predict_contract(adapter):
    rows = _rows(VALUES)
    fitted = fit_model(adapter, rows, {}, 42, CUTOFF, "month")
    preds = predict_model(fitted, H, CUTOFF)
    assert [p["horizon"] for p in preds] == H
    for p in preds:
        assert p["yhat"] is not None and p["yhat"] >= 0
        assert p["lower_bound"] is not None and p["upper_bound"] is not None
        assert p["lower_bound"] <= p["upper_bound"]
        assert p["lower_bound"] >= 0
        assert p["interval_level"] == 0.95
    assert fitted.artifact_sha256
    assert fitted.adapter_version


@pytest.mark.parametrize("adapter", ["negbin_dglm.v1", "hurdle_count_ss.v1",
                                     "last_value", "rolling_mean", "drift"])
def test_fixed_seed_reproducible(adapter):
    rows = _rows(VALUES)
    f1 = fit_model(adapter, rows, {}, 123, CUTOFF, "month")
    f2 = fit_model(adapter, rows, {}, 123, CUTOFF, "month")
    assert f1.artifact_sha256 == f2.artifact_sha256
    assert predict_model(f1, H, CUTOFF) == predict_model(f2, H, CUTOFF)


def test_missing_observations_stay_null():
    values = list(VALUES)
    values[5] = None
    values[17] = None
    rows = _rows(values)
    fitted = fit_model("negbin_dglm.v1", rows, {}, 7, CUTOFF, "month")
    # missing values are never zero-filled: state differs from zero-filled run
    zero_filled = _rows([0 if v is None else v for v in values])
    fitted_zero = fit_model("negbin_dglm.v1", zero_filled, {}, 7, CUTOFF, "month")
    assert fitted.artifact_sha256 != fitted_zero.artifact_sha256
    preds = predict_model(fitted, H, CUTOFF)
    assert all(p["yhat"] is not None and p["yhat"] >= 0 for p in preds)


def test_seasonal_naive_not_eligible_without_two_cycles():
    rows = _rows(VALUES[:36], frequency="week")  # 36 < 2*52
    fitted = fit_model("seasonal_naive", rows, {}, 1, rows[-1].period_start, "week")
    assert fitted.state["not_eligible"] == SEASONAL_NAIVE_NOT_ELIGIBLE
    preds = predict_model(fitted, H, rows[-1].period_start)
    assert all(p["yhat"] is None and p["lower_bound"] is None for p in preds)
    assert all(p["not_eligible"] == SEASONAL_NAIVE_NOT_ELIGIBLE for p in preds)


def test_seasonal_naive_eligible_with_two_cycles():
    rows = _rows(VALUES * 4, frequency="week")[:120]  # 120 >= 104
    fitted = fit_model("seasonal_naive", rows, {}, 1, rows[-1].period_start, "week")
    assert fitted.state["not_eligible"] is None
    preds = predict_model(fitted, H, rows[-1].period_start)
    assert all(p["yhat"] is not None and p["yhat"] >= 0 for p in preds)


def test_baseline_null_when_lag_missing():
    rows = _rows(VALUES[:30])  # 30 months >= 24 -> eligible, lag present
    fitted = fit_model("seasonal_naive", rows, {}, 1, rows[-1].period_start, "month")
    preds = predict_model(fitted, H, rows[-1].period_start)
    assert all(p["yhat"] is not None for p in preds)


def test_nb_quantiles_ordered_and_nonnegative():
    lo, hi = nb_quantiles(10.0, 8.0)
    assert 0 <= lo <= hi
    lo2, hi2 = nb_quantiles(0.0, 8.0)
    assert lo2 == 0 and hi2 >= 0


def test_hurdle_zero_probability_present():
    values = [0, 0, 3, 0, 5, 0, 0, 2, 0, 4, 0, 0] * 3
    rows = _rows(values)
    fitted = fit_model("hurdle_count_ss.v1", rows, {}, 5, rows[-1].period_start, "month")
    preds = predict_model(fitted, H, rows[-1].period_start)
    assert all(0.0 <= p["zero_probability"] <= 1.0 for p in preds)


def test_parameter_bounds_clipped():
    rows = _rows(VALUES)
    fitted = fit_model("negbin_dglm.v1", rows, {"discount": 5.0, "shape": -3.0}, 1, CUTOFF, "month")
    assert 0.8 <= fitted.state["discount"] <= 0.999
    assert 0.5 <= fitted.state["nb_shape"] <= 500.0


def test_unknown_adapter_rejected():
    with pytest.raises(ValueError, match="UNKNOWN_ADAPTER"):
        fit_model("evil.module:os.system", _rows(VALUES), {}, 1, CUTOFF, "month")
