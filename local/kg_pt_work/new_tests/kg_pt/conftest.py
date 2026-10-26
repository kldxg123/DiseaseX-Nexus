# ruff: noqa: E501
"""Shared fixtures for kg_pt tests.

The series here is SYNTHETIC test input: a deterministic monthly count series
(trend + yearly seasonality + deterministic jitter) shaped like the demo
series (240 months, 2005-01..2024-12). It is NOT real AUT data and must never
be presented as such.
"""

import math
from datetime import date

import pytest

from epidemic_core.forecast_model_kg.dual_loop import period_add
from epidemic_core.kg_r3c.execution import TrainingRow

SYNTH_START = date(2005, 1, 1)
N_MONTHS = 240


def synthetic_values(n: int = N_MONTHS) -> list[int]:
    """Deterministic synthetic monthly counts (trend + season + jitter)."""
    return [int(round(40 + 0.08 * i + 12 * math.sin(2 * math.pi * (i % 12) / 12) + (i * 7) % 5)) for i in range(n)]


def make_rows(values, start: date = SYNTH_START, frequency: str = "month") -> list[TrainingRow]:
    rows = []
    for i, v in enumerate(values):
        p = period_add(start, frequency, i)
        rows.append(
            TrainingRow(
                period_start=p,
                period_end=p,
                value=None if v is None else float(v),
                observation_id=f"syn-{i}",
                revision=1,
            )
        )
    return rows


@pytest.fixture(scope="session")
def synthetic_series() -> list[TrainingRow]:
    """240 synthetic monthly rows. Treat as immutable; copy before mutating."""
    return make_rows(synthetic_values(N_MONTHS))


@pytest.fixture(scope="session")
def synthetic_periods(synthetic_series) -> list[date]:
    return [r.period_start for r in synthetic_series]
