"""Recompute WAPE/MAE from out/paired_comparison.csv and compare with the
metrics file (test split, overall and per horizon)."""

from __future__ import annotations

import csv
import math

import benchlib as b
import pytest
from conftest import OUT_DIR

TOL = 1e-9


@pytest.fixture(scope="module")
def paired_rows() -> list[dict[str, str]]:
    with (OUT_DIR / "paired_comparison.csv").open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


@pytest.fixture(scope="module")
def metrics_rows() -> list[dict[str, str]]:
    with (OUT_DIR / "metrics_by_model_and_horizon.csv").open(
        newline="", encoding="utf-8"
    ) as handle:
        return list(csv.DictReader(handle))


def _recompute(rows: list[dict[str, str]], model: str, horizon: int | None) -> tuple[float, float]:
    sel = [
        r
        for r in rows
        if int(r["origin_index"]) in b.TEST_ORIGINS
        and (horizon is None or int(r["horizon"]) == horizon)
    ]
    abs_err = [abs(float(r["actual"]) - float(r[model])) for r in sel]
    mae = sum(abs_err) / len(abs_err)
    wape = sum(abs_err) / sum(abs(float(r["actual"])) for r in sel)
    return wape, mae


@pytest.mark.parametrize("model", b.MODEL_IDS)
def test_overall_wape_mae(
    paired_rows: list[dict[str, str]], metrics_rows: list[dict[str, str]], model: str
) -> None:
    wape, mae = _recompute(paired_rows, model, None)
    row = next(
        r
        for r in metrics_rows
        if r["split"] == "test" and r["model"] == model and r["horizon"] == "all"
    )
    assert math.isclose(wape, float(row["WAPE"]), rel_tol=TOL, abs_tol=TOL)
    assert math.isclose(mae, float(row["MAE"]), rel_tol=TOL, abs_tol=TOL)


@pytest.mark.parametrize("model", b.MODEL_IDS)
@pytest.mark.parametrize("horizon", [1, 2, 3, 4])
def test_per_horizon_wape(
    paired_rows: list[dict[str, str]],
    metrics_rows: list[dict[str, str]],
    model: str,
    horizon: int,
) -> None:
    wape, _mae = _recompute(paired_rows, model, horizon)
    row = next(
        r
        for r in metrics_rows
        if r["split"] == "test" and r["model"] == model and r["horizon"] == str(horizon)
    )
    assert math.isclose(wape, float(row["WAPE"]), rel_tol=TOL, abs_tol=TOL)
    assert int(row["n"]) == {1: 50, 2: 49, 3: 48, 4: 47}[horizon]
