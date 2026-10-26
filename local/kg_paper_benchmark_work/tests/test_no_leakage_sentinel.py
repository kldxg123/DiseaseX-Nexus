"""Future-sentinel: poisoning values[e:] must not change any origin-e forecast."""

from __future__ import annotations

import benchlib as b
import pytest

SAMPLED_ORIGINS = (320, 390)
WEIGHTS = (0.5, 0.3, 0.2)


@pytest.fixture(scope="module")
def series() -> b.Series:
    return b.load_series()


@pytest.mark.parametrize("origin", SAMPLED_ORIGINS)
@pytest.mark.parametrize("model", b.MODEL_IDS)
def test_no_future_leakage(series: b.Series, model: str, origin: int) -> None:
    base = b.build_registry(series.values, b.KG_DEFAULT_PARAMS, WEIGHTS)
    if model not in base.fns:
        pytest.skip(f"{model} not registered (optional dependency missing)")
    poisoned = b.build_registry(
        b.sentinel_mutate(series.values, origin), b.KG_DEFAULT_PARAMS, WEIGHTS
    )
    assert b.forecasts_equal(base.fns[model](origin), poisoned.fns[model](origin)), (
        f"{model} at origin {origin} changed when the future was poisoned"
    )
