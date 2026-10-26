"""Exactly-once ledger: validation selection froze before the single test pass."""

from __future__ import annotations

import json

import pytest
from conftest import OUT_DIR


@pytest.fixture(scope="module")
def overall() -> dict[str, object]:
    with (OUT_DIR / "metrics_overall.json").open(encoding="utf-8") as handle:
        doc: dict[str, object] = json.load(handle)
    return doc


def test_exactly_once_ledger(overall: dict[str, object]) -> None:
    ledger = overall["exactly_once_ledger"]
    assert isinstance(ledger, dict)
    assert ledger["validation_selection_completed"] is True
    assert ledger["test_evaluated_count"] == 1


def test_selection_artifacts_marked() -> None:
    with (OUT_DIR / "qwen_grid.csv").open(encoding="utf-8") as handle:
        lines = handle.read().strip().splitlines()
    assert len(lines) == 1 + 64  # whitelist grid: 4 x 4 x 4
    selected = [ln for ln in lines[1:] if ln.rsplit(",", 1)[-1].rstrip(".0") == "1"]
    assert len(selected) == 1
    with (OUT_DIR / "ensemble_grid.csv").open(encoding="utf-8") as handle:
        lines = handle.read().strip().splitlines()
    assert len(lines) == 1 + 231  # 0.05 simplex: C(20+2, 2) = 231
    selected = [ln for ln in lines[1:] if ln.rsplit(",", 1)[-1].rstrip(".0") == "1"]
    assert len(selected) == 1


def test_sentinel_and_determinism_passed() -> None:
    with (OUT_DIR / "leakage_and_sentinel_results.json").open(encoding="utf-8") as handle:
        doc = json.load(handle)
    assert doc["future_sentinel"]["passed"] is True
    assert doc["split_integrity"]["passed"] is True
    assert doc["no_zero_fill"]["passed"] is True
    det = doc["determinism"]
    assert det["identical"] is True
    assert det["run1_predictions_csv_sha256"] == det["run2_predictions_csv_sha256"]
