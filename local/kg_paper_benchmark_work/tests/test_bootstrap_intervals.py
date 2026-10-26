"""Residual-bootstrap interval sanity for the multi-wave sub-epidemic models."""

from __future__ import annotations

import json

import benchlib as b
import pytest
from conftest import OUT_DIR

BOOTSTRAP_MODELS = (
    "paper_subepidemic_second",
    "paper_subepidemic_ensemble_weighted",
    "paper_subepidemic_ensemble_unweighted",
)
ORIGIN = 400


@pytest.fixture(scope="module")
def series() -> b.Series:
    return b.load_series()


def test_bootstrap_intervals_well_formed(series: b.Series) -> None:
    """Intervals are deterministic, non-negative, and ordered at a sample origin."""
    stats = b.EnsembleSubepidemicStats()
    cache = b.make_candidate_cache(series.values)
    for mode in ("second", "weighted", "unweighted"):
        fn = b.make_subepidemic_multi_fn(mode, cache, stats)
        fc1 = fn(ORIGIN)
        fc2 = fn(ORIGIN)
        assert b.forecasts_equal(fc1, fc2), f"{mode} bootstrap not deterministic"
        for h in b.HORIZONS:
            p = fc1[h]
            assert 0.0 <= p.lower <= p.upper, (mode, h, p)
            assert p.lower <= max(p.yhat, p.lower), (mode, h, p)


def test_bootstrap_coverage_reasonable_on_validation() -> None:
    """Validation coverage_95 of the bootstrap-interval models stays in [0.5, 1.0]."""
    with (OUT_DIR / "metrics_overall.json").open(encoding="utf-8") as handle:
        overall = json.load(handle)
    validation = overall["metrics"]["validation"]
    for model in BOOTSTRAP_MODELS:
        coverage = float(validation[model]["all"]["coverage_95"])
        assert 0.5 <= coverage <= 1.0, (model, coverage)
