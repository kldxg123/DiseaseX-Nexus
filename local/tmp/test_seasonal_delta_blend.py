from __future__ import annotations

import math
from datetime import date, timedelta

import pytest

from epidemic_core.kg_qwen.adapters_ext import register_extended_models
from epidemic_core.kg_qwen.contracts_ext import all_contracts_for
from epidemic_core.kg_qwen.seasonal_delta_blend import SEASONAL_DELTA_BLEND_ID
from epidemic_core.kg_r3c import execution
from epidemic_core.kg_r3c.execution import TrainingRow


def weekly_rows(n: int = 180, missing: set[int] | None = None) -> list[TrainingRow]:
    missing = missing or set()
    start = date(2020, 1, 6)
    return [
        TrainingRow(
            period_start=start + timedelta(weeks=index),
            period_end=None,
            value=None if index in missing else float(20 + index % 52 + index / 100),
        )
        for index in range(n)
    ]


def test_exact_prefix_formula_and_determinism() -> None:
    register_extended_models()
    rows = weekly_rows()
    params = {"seasonal_years": 1, "recent_window": 4, "blend_weight": 0.4}
    cutoff = rows[-1].period_start
    first = execution.fit_model(SEASONAL_DELTA_BLEND_ID, rows, params, 20260814, cutoff, "week")
    second = execution.fit_model(SEASONAL_DELTA_BLEND_ID, rows, params, 20260814, cutoff, "week")
    assert first.artifact_sha256 == second.artifact_sha256
    prediction = execution.predict_model(first, [1], cutoff)[0]
    values = [float(row.value) for row in rows if row.value is not None]
    seasonal = values[-52]
    recent = sum(values[-4:]) / 4
    previous = sum(values[-56:-52]) / 4
    delta = max(seasonal + recent - previous, 0.0)
    expected = 0.4 * delta + 0.6 * recent
    assert prediction["yhat"] == pytest.approx(expected, abs=1e-6)
    assert prediction["lower_bound"] <= prediction["yhat"] <= prediction["upper_bound"]


def test_missing_required_recent_value_stays_null() -> None:
    register_extended_models()
    rows = weekly_rows(missing={179})
    fitted = execution.fit_model(
        SEASONAL_DELTA_BLEND_ID,
        rows,
        {"seasonal_years": 1, "recent_window": 4, "blend_weight": 0.4},
        20260814,
        rows[-1].period_start,
        "week",
    )
    predictions = execution.predict_model(fitted, [1, 2, 3, 4], rows[-1].period_start)
    assert all(item["yhat"] is None for item in predictions)
    assert all(item["interval_method"] == "NONE_MISSING_LAG" for item in predictions)


def test_nonnegative_finite_predictions_and_contracts() -> None:
    register_extended_models()
    rows = weekly_rows()
    fitted = execution.fit_model(
        SEASONAL_DELTA_BLEND_ID,
        rows,
        {"seasonal_years": 9, "recent_window": 9, "blend_weight": 2.0},
        20260814,
        rows[-1].period_start,
        "week",
    )
    assert fitted.state["seasonal_years"] == 3
    assert fitted.state["recent_window"] == 8
    assert fitted.state["blend_weight"] == 1.0
    predictions = execution.predict_model(fitted, [1, 2, 3, 4], rows[-1].period_start)
    assert all(item["yhat"] is not None and math.isfinite(item["yhat"]) for item in predictions)
    contracts = all_contracts_for(SEASONAL_DELTA_BLEND_ID, "week")
    assert {item.parameter_name for item in contracts} == {
        "seasonal_years",
        "recent_window",
        "blend_weight",
    }
