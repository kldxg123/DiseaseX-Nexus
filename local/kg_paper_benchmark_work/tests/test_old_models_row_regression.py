"""Byte-level regression lock for the original ten models.

The gap-closure round (2026-08-19) added paper_subepidemic_second,
paper_subepidemic_ensemble_weighted, paper_subepidemic_ensemble_unweighted and
paper_prophet.  The hard invariant: every rolling_origin_predictions.csv row
of the ORIGINAL ten models must be byte-identical to the round-1 output
(snapshot in out_prev_snapshot/, sha256 pinned below).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import benchlib as b
import pytest
from conftest import OUT_DIR

SNAPSHOT_DIR = Path(__file__).resolve().parent.parent / "out_prev_snapshot"
EXPECTED_SNAPSHOT_SHA256 = "2b69749a4cf269119b01984e0f27cbfdda6171dac5273f700a06c92bf47f336f"


def _old_model_rows(path: Path) -> dict[tuple[int, str, int], str]:
    """Map (origin_index, model, horizon) -> the exact CSV line."""
    lines = path.read_text(encoding="utf-8").splitlines()
    rows: dict[tuple[int, str, int], str] = {}
    for line in lines[1:]:
        cells = line.split(",")
        origin, model, horizon = int(cells[0]), cells[6], int(cells[2])
        if model in b.ORIGINAL_MODEL_IDS:
            rows[(origin, model, horizon)] = line
    return rows


@pytest.fixture(scope="module")
def snapshot_rows() -> dict[tuple[int, str, int], str]:
    path = SNAPSHOT_DIR / "rolling_origin_predictions.csv"
    assert path.exists(), "round-1 snapshot out_prev_snapshot/ missing"
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    assert digest == EXPECTED_SNAPSHOT_SHA256, (
        f"snapshot sha256 drifted: {digest} != {EXPECTED_SNAPSHOT_SHA256}"
    )
    return _old_model_rows(path)


@pytest.fixture(scope="module")
def current_rows() -> dict[tuple[int, str, int], str]:
    path = OUT_DIR / "rolling_origin_predictions.csv"
    assert path.exists(), "run src/run_experiment.py first"
    return _old_model_rows(path)


def test_same_old_model_row_keys(
    snapshot_rows: dict[tuple[int, str, int], str],
    current_rows: dict[tuple[int, str, int], str],
) -> None:
    assert set(snapshot_rows) == set(current_rows)
    # 10 models x (200 validation pairs + 194 test pairs) = 3940 rows.
    assert len(snapshot_rows) == 10 * (200 + 50 + 49 + 48 + 47)


def test_old_models_rows_byte_identical(
    snapshot_rows: dict[tuple[int, str, int], str],
    current_rows: dict[tuple[int, str, int], str],
) -> None:
    drifted = [key for key, line in snapshot_rows.items() if current_rows.get(key) != line]
    assert not drifted, f"{len(drifted)} old-model rows changed, e.g. {drifted[:5]}"
