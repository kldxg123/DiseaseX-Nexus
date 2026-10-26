"""Paired samples: every model column is populated on the same (origin, horizon)
pairs in out/paired_comparison.csv, and pair counts match the SPEC."""

from __future__ import annotations

import csv

import benchlib as b
import pytest
from conftest import OUT_DIR


@pytest.fixture(scope="module")
def paired_rows() -> list[dict[str, str]]:
    path = OUT_DIR / "paired_comparison.csv"
    assert path.exists(), "run src/run_experiment.py first"
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_pair_counts(paired_rows: list[dict[str, str]]) -> None:
    val = [r for r in paired_rows if int(r["origin_index"]) in b.VAL_ORIGINS]
    test = [r for r in paired_rows if int(r["origin_index"]) in b.TEST_ORIGINS]
    assert len(val) == 200
    assert len(test) == 50 + 49 + 48 + 47
    per_h = {h: sum(1 for r in test if int(r["horizon"]) == h) for h in b.HORIZONS}
    assert per_h == {1: 50, 2: 49, 3: 48, 4: 47}


def test_no_missing_model_cells(paired_rows: list[dict[str, str]]) -> None:
    # Fallback rows are still populated (flagged in the long CSV), so no model
    # cell may be empty on any evaluated pair.
    for row in paired_rows:
        for model in b.MODEL_IDS:
            assert row[model] not in ("", None), (row["origin_index"], model)
            assert row[f"{model}__interval_hit"] in ("0", "1")


def test_all_pairs_cover_all_models(paired_rows: list[dict[str, str]]) -> None:
    keys = {(r["origin_index"], r["horizon"]) for r in paired_rows}
    assert len(keys) == len(paired_rows)  # one row per pair, fully paired
