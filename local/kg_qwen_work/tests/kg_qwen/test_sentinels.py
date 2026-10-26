"""Tests for the anti-leakage sentinel suite (pure, synthetic data).

The synthetic series carries a hidden/test segment whose values are shifted
far outside the train+validation range, so the qwen-context sentinel has
distinctive hidden-only numbers to hunt for. All sentinels execute real code
paths; these tests assert the whole suite passes and drill into the
plan-validator sentinels individually.
"""

from __future__ import annotations

from datetime import date

import pytest

from epidemic_core.forecast_model_kg.dual_loop import period_add
from epidemic_core.kg_pt.pipeline import compute_snapshot_sha256
from epidemic_core.kg_pt.split import TemporalSplit, make_split
from epidemic_core.kg_qwen import sentinels
from epidemic_core.kg_qwen.plan_contract import deterministic_fallback_plan, validate_plan
from epidemic_core.kg_r3c.execution import TrainingRow

TRACK_KEY = "SYN:demo:synthetic_cases:month"
FREQUENCY = "month"
TRAIN_N = 168
VAL_N = 36
TEST_N = 36
N_ROWS = TRAIN_N + VAL_N + TEST_N

PATTERN = [30.0, 25.0, 20.0, 15.0, 12.0, 10.0, 12.0, 15.0, 20.0, 28.0, 35.0, 40.0]

EXPECTED_SENTINELS = {
    "test_label_sentinel",
    "future_observation_sentinel",
    "parameter_selection_sentinel",
    "preprocessing_leakage_sentinel",
    "qwen_context_sentinel",
    "ensemble_weight_sentinel",
    "checkpoint_replay_sentinel",
    "snapshot_immutability_sentinel",
    "prompt_injection_sentinel",
    "unregistered_param_sentinel",
    "bound_violation_sentinel",
}


@pytest.fixture(scope="module")
def rows() -> list[TrainingRow]:
    """Seasonal monthly series; hidden-segment values are shifted to a range
    (1000+) that cannot collide with any train+validation statistic."""
    start = date(2005, 1, 1)
    out: list[TrainingRow] = []
    for i in range(N_ROWS):
        value = PATTERN[i % 12] + i // 12 * 1.5 if i < TRAIN_N + VAL_N else 1000.0 + i
        out.append(TrainingRow(period_start=period_add(start, FREQUENCY, i), period_end=None, value=value))
    return out


@pytest.fixture(scope="module")
def split(rows: list[TrainingRow]) -> TemporalSplit:
    return make_split([r.period_start for r in rows], TRAIN_N, VAL_N, TEST_N)


@pytest.fixture(scope="module")
def snapshot_sha(rows: list[TrainingRow]) -> str:
    return compute_snapshot_sha256(rows)


@pytest.fixture(scope="module")
def suite(rows: list[TrainingRow], split: TemporalSplit, snapshot_sha: str) -> dict[str, sentinels.SentinelResult]:
    results = sentinels.run_all_sentinels(
        track_key=TRACK_KEY,
        frequency=FREQUENCY,
        rows=rows,
        split=split,
        snapshot_sha256=snapshot_sha,
    )
    return {r.name: r for r in results}


def test_all_sentinels_pass(suite: dict[str, sentinels.SentinelResult]) -> None:
    assert set(suite) == EXPECTED_SENTINELS
    failures = {name: r.detail for name, r in suite.items() if not r.passed}
    assert not failures, f"sentinels failed: {failures}"


def test_every_sentinel_carries_hash_evidence(suite: dict[str, sentinels.SentinelResult]) -> None:
    for name, result in suite.items():
        assert result.evidence_sha256, f"{name} has no evidence hash"
        assert len(result.evidence_sha256) == 64
        assert result.detail, f"{name} has empty detail"


def test_prompt_injection_sentinel_rejects_all_five(snapshot_sha: str) -> None:
    result = sentinels.prompt_injection_sentinel(snapshot_sha256=snapshot_sha, frequency=FREQUENCY)
    assert result.passed, result.detail
    assert "5 injection samples" in result.detail


def test_unregistered_param_sentinel(snapshot_sha: str) -> None:
    result = sentinels.unregistered_param_sentinel(snapshot_sha256=snapshot_sha, frequency=FREQUENCY)
    assert result.passed, result.detail
    assert "PLAN_UNREGISTERED_PARAMETER" in result.detail


def test_bound_violation_sentinel(snapshot_sha: str) -> None:
    result = sentinels.bound_violation_sentinel(snapshot_sha256=snapshot_sha, frequency=FREQUENCY)
    assert result.passed, result.detail
    assert "PLAN_BOUND_VIOLATION" in result.detail


def test_clean_fallback_plan_validates(snapshot_sha: str) -> None:
    """Sanity anchor: the deterministic fallback plan itself is valid."""
    plan = deterministic_fallback_plan(snapshot_sha, FREQUENCY, "sentinel test")
    assert validate_plan(plan, expected_snapshot_sha256=snapshot_sha, frequency=FREQUENCY) == []


def test_qwen_context_sentinel_actually_checked_hidden_values(
    suite: dict[str, sentinels.SentinelResult], rows: list[TrainingRow]
) -> None:
    result = suite["qwen_context_sentinel"]
    assert result.passed, result.detail
    # All 36 hidden values are outside the trainval range, so the sentinel
    # must have checked exactly 36 hidden-only numbers.
    assert "36 hidden-only values checked -> 0 leaked" in result.detail
