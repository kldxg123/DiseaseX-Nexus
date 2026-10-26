"""Determinism: key methods rerun on the same input give identical output."""

from __future__ import annotations

import benchlib as b
import pytest

ORIGIN = 400


@pytest.fixture(scope="module")
def series() -> b.Series:
    return b.load_series()


@pytest.mark.parametrize(
    "model",
    [
        "paper_arima",
        "paper_gam",
        "paper_slr",
        "paper_subepidemic_top",
        "project_kg_primary",
        "project_qwen_tuned",
        "project_kg_ensemble",
        "last_value",
        "seasonal_naive",
        "rolling_mean_4",
        # gap-closure round: multi-wave sub-epidemic ensembles + prophet.
        "paper_subepidemic_second",
        "paper_subepidemic_ensemble_weighted",
        "paper_subepidemic_ensemble_unweighted",
        pytest.param(
            "paper_prophet",
            marks=pytest.mark.skipif(
                not b.prophet_available(), reason="prophet not installed"
            ),
        ),
    ],
)
def test_method_deterministic(series: b.Series, model: str) -> None:
    weights = (0.5, 0.3, 0.2)
    reg1 = b.build_registry(series.values, b.KG_DEFAULT_PARAMS, weights)
    reg2 = b.build_registry(series.values, b.KG_DEFAULT_PARAMS, weights)
    assert b.forecasts_equal(reg1.fns[model](ORIGIN), reg2.fns[model](ORIGIN))


def test_pipeline_selection_deterministic(series: b.Series) -> None:
    """The Qwen grid scoring helper is pure: same input, same scores."""
    e = 350
    fc1 = b.forecast_project(series.values, e, b.KG_DEFAULT_PARAMS, b.SEED)
    fc2 = b.forecast_project(series.values, e, b.KG_DEFAULT_PARAMS, b.SEED)
    assert b.forecasts_equal(fc1, fc2)
