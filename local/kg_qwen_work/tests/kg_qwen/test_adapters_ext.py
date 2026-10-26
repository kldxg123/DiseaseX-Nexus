"""Tests for the extended kg_qwen whitelist candidate models.

Covers the four adapters in ``kg_qwen.adapters_ext`` on a synthetic monthly
series (seasonality + trend + missing values, pure-python deterministic
generation): fit/predict shape, non-negative finite outputs, determinism
(same seed + input -> identical artifact_sha256), NULL prediction on missing
lag features, seasonal-naive not-eligible pass-through inside the ensemble,
ensemble weight endpoints, register idempotency and Fourier K boundaries.
"""

from __future__ import annotations

import math
from datetime import date

import pytest

from epidemic_core.kg_qwen.adapters_ext import (
    EXTENDED_ADAPTER_IDS,
    FOURIER_NEGBIN_GLM_ID,
    LAGGED_COUNT_BOOSTING_ID,
    SEASONAL_ENSEMBLE_ID,
    register_extended_models,
)
from epidemic_core.kg_r3c import execution
from epidemic_core.kg_r3c.execution import TrainingRow

SEED = 20260811
HORIZONS = [1, 2, 3, 4]
N_MONTHS = 240
MISSING_IDX = (50, 101, 150)


def make_monthly_rows(n: int = N_MONTHS, missing: tuple[int, ...] = MISSING_IDX) -> list[TrainingRow]:
    """Deterministic synthetic monthly counts with seasonality + trend."""
    rows = []
    for i in range(n):
        month = i % 12 + 1
        year = 2005 + i // 12
        seasonal = 1.0 + 0.4 * math.sin(2.0 * math.pi * (month - 1) / 12.0)
        trend = 1.0 + 0.15 * (i / n)
        value = 30.0 * seasonal * trend + 2.0 * math.sin(i * 0.7)
        rows.append(
            TrainingRow(
                period_start=date(year, month, 1),
                period_end=None,
                value=None if i in missing else float(max(round(value), 0)),
            )
        )
    return rows


def fit_and_predict(adapter_id: str, rows: list[TrainingRow], params: dict | None = None):
    cutoff = rows[-1].period_start
    fitted = execution.fit_model(adapter_id, rows, params or {}, SEED, cutoff, "month")
    preds = execution.predict_model(fitted, HORIZONS, cutoff)
    return fitted, preds


register_extended_models()  # module scope: registration must survive import order


@pytest.fixture()
def monthly_rows() -> list[TrainingRow]:
    return make_monthly_rows()


# ------------------------------------------------------------------ shape/finite
@pytest.mark.parametrize("adapter_id", EXTENDED_ADAPTER_IDS)
def test_fit_predict_shape_nonnegative_finite(adapter_id: str, monthly_rows: list[TrainingRow]) -> None:
    fitted, preds = fit_and_predict(adapter_id, monthly_rows)
    assert fitted.adapter_id == adapter_id
    assert fitted.train_row_count == len(monthly_rows)
    assert len(preds) == len(HORIZONS)
    for pred, h in zip(preds, HORIZONS, strict=False):
        assert set(pred) == {
            "horizon",
            "yhat",
            "lower_bound",
            "upper_bound",
            "interval_level",
            "interval_method",
        }
        assert pred["horizon"] == h
        assert pred["interval_level"] == pytest.approx(execution.INTERVAL_LEVEL)
        yhat = pred["yhat"]
        assert yhat is not None
        assert math.isfinite(yhat) and yhat >= 0.0
        lo, hi = pred["lower_bound"], pred["upper_bound"]
        assert lo is not None and hi is not None
        assert 0.0 <= lo <= hi
        assert math.isfinite(lo) and math.isfinite(hi)


# ------------------------------------------------------------------ determinism
@pytest.mark.parametrize("adapter_id", EXTENDED_ADAPTER_IDS)
def test_deterministic_artifact_sha(adapter_id: str, monthly_rows: list[TrainingRow]) -> None:
    first, _ = fit_and_predict(adapter_id, monthly_rows)
    second, _ = fit_and_predict(adapter_id, monthly_rows)
    assert first.artifact_sha256 == second.artifact_sha256


def test_fit_respects_cutoff(monthly_rows: list[TrainingRow]) -> None:
    cutoff = monthly_rows[167].period_start
    full_window = execution.fit_model(
        "seasonal_negbin_glm.v1", monthly_rows, {}, SEED, cutoff, "month"
    )
    sliced = execution.fit_model(
        "seasonal_negbin_glm.v1", monthly_rows[:168], {}, SEED, cutoff, "month"
    )
    assert full_window.train_row_count == 168
    assert full_window.artifact_sha256 == sliced.artifact_sha256


# --------------------------------------------------------------- missing -> NULL
def test_boosting_missing_lag_returns_null(monthly_rows: list[TrainingRow]) -> None:
    tail = monthly_rows[-1]
    broken = monthly_rows[:-1] + [
        TrainingRow(period_start=tail.period_start, period_end=None, value=None)
    ]
    _, preds = fit_and_predict(LAGGED_COUNT_BOOSTING_ID, broken)
    for pred in preds:
        assert pred["yhat"] is None
        assert pred["lower_bound"] is None
        assert pred["upper_bound"] is None
        assert pred["interval_method"] == "NONE_MISSING_LAG"


def test_training_skips_missing_values(monthly_rows: list[TrainingRow]) -> None:
    fitted = execution.fit_model(
        "seasonal_negbin_glm.v1",
        monthly_rows,
        {},
        SEED,
        monthly_rows[-1].period_start,
        "month",
    )
    assert fitted.state["n_observed"] == N_MONTHS - len(MISSING_IDX)


# ------------------------------------------------------- seasonal_naive NULL 透传
def test_ensemble_degenerates_when_seasonal_naive_not_eligible() -> None:
    rows = make_monthly_rows(n=18, missing=())  # < 2 full cycles of 12
    cutoff = rows[-1].period_start
    fitted = execution.fit_model(SEASONAL_ENSEMBLE_ID, rows, {}, SEED, cutoff, "month")
    preds = execution.predict_model(fitted, HORIZONS, cutoff)
    sn = execution.fit_baseline("seasonal_naive", rows, {}, SEED, cutoff, "month")
    assert sn.state["not_eligible"] == execution.SEASONAL_NAIVE_NOT_ELIGIBLE
    component = execution.fit_model("negbin_dglm.v1", rows, {}, SEED, cutoff, "month")
    comp_preds = execution.predict_model(component, HORIZONS, cutoff)
    for pred, comp in zip(preds, comp_preds, strict=False):
        # seasonal_naive is NULL -> ensemble passes the component through
        assert pred["yhat"] is not None
        assert pred["yhat"] == pytest.approx(comp["yhat"])


# ------------------------------------------------------------- ensemble endpoints
@pytest.mark.parametrize(
    ("weight", "component_id"),
    [(1.0, "seasonal_naive"), (0.0, "negbin_dglm.v1")],
)
def test_ensemble_weight_endpoints(
    weight: float, component_id: str, monthly_rows: list[TrainingRow]
) -> None:
    cutoff = monthly_rows[-1].period_start
    fitted = execution.fit_model(
        SEASONAL_ENSEMBLE_ID, monthly_rows, {"weight": weight}, SEED, cutoff, "month"
    )
    preds = execution.predict_model(fitted, HORIZONS, cutoff)
    direct = execution.fit_model(component_id, monthly_rows, {}, SEED, cutoff, "month")
    direct_preds = execution.predict_model(direct, HORIZONS, cutoff)
    for pred, ref in zip(preds, direct_preds, strict=False):
        assert pred["yhat"] == pytest.approx(ref["yhat"])
        assert pred["lower_bound"] == pytest.approx(ref["lower_bound"])
        assert pred["upper_bound"] == pytest.approx(ref["upper_bound"])


def test_ensemble_unknown_component_falls_back(monthly_rows: list[TrainingRow]) -> None:
    cutoff = monthly_rows[-1].period_start
    fitted = execution.fit_model(
        SEASONAL_ENSEMBLE_ID,
        monthly_rows,
        {"weight": 0.5, "component_adapter": "not_a_model.v9"},
        SEED,
        cutoff,
        "month",
    )
    assert fitted.state["component_adapter"] == "negbin_dglm.v1"


# -------------------------------------------------------------------- registry
def test_register_extended_models_idempotent() -> None:
    before_fits = dict(execution.MODEL_FITS)
    before_predicts = dict(execution.MODEL_PREDICTS)
    register_extended_models()
    register_extended_models()
    assert before_fits == execution.MODEL_FITS
    assert before_predicts == execution.MODEL_PREDICTS
    for adapter_id in EXTENDED_ADAPTER_IDS:
        assert adapter_id in execution.MODEL_FITS
        assert adapter_id in execution.MODEL_PREDICTS
    for baseline_id in execution.BASELINE_IDS:
        assert baseline_id not in execution.MODEL_FITS  # baselines stay on their own path


# ----------------------------------------------------------------- fourier K
@pytest.mark.parametrize("k", [1, 2, 3])
def test_fourier_k_candidates(k: int, monthly_rows: list[TrainingRow]) -> None:
    fitted, preds = fit_and_predict(FOURIER_NEGBIN_GLM_ID, monthly_rows, {"K": k})
    assert fitted.state["K"] == k
    assert all(p["yhat"] is not None and p["yhat"] >= 0.0 for p in preds)


@pytest.mark.parametrize(("given", "clipped"), [(0, 1), (7, 3)])
def test_fourier_k_clipped_to_bounds(
    given: int, clipped: int, monthly_rows: list[TrainingRow]
) -> None:
    fitted, _ = fit_and_predict(FOURIER_NEGBIN_GLM_ID, monthly_rows, {"K": given})
    assert fitted.state["K"] == clipped


# -------------------------------------------------------------- weekly profile
def test_weekly_frequency_awareness() -> None:
    rows = []
    start = date(2018, 1, 1)
    for i in range(120):
        ps = date.fromordinal(start.toordinal() + 7 * i)
        value = 20.0 + 8.0 * math.sin(2.0 * math.pi * (i % 52) / 52.0)
        rows.append(
            TrainingRow(
                period_start=ps,
                period_end=None,
                value=None if i == 60 else float(max(round(value), 0)),
            )
        )
    cutoff = rows[-1].period_start
    fitted = execution.fit_model("seasonal_negbin_glm.v1", rows, {}, SEED, cutoff, "week")
    assert fitted.state["frequency"] == "week"
    assert fitted.state["period"] == 52
    assert len(fitted.state["coefficients"]) == 52  # intercept + 51 dummies
    preds = execution.predict_model(fitted, HORIZONS, cutoff)
    assert all(p["yhat"] is not None and p["yhat"] >= 0.0 for p in preds)
