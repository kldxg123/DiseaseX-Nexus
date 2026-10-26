"""Tests for the Qwen multi-round autotuning loop (pure, synthetic data).

A 240-month seasonal series is split 168/36/36 exactly like the Track-A
demo. The planner is a fake callable returning pre-built plans, so the loop
logic (validation retry, fallback, budget clamping, round summaries, target
judgement, champion honesty, checkpoint callbacks, determinism) is exercised
without any Qwen runtime.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any

import pytest

from epidemic_core.forecast_model_kg.dual_loop import period_add
from epidemic_core.kg_pt.pipeline import compute_snapshot_sha256
from epidemic_core.kg_pt.split import TemporalSplit, make_split
from epidemic_core.kg_qwen import loop as kq_loop
from epidemic_core.kg_qwen.agent_tools import ToolContext
from epidemic_core.kg_qwen.loop import LoopResult, QwenProposal, RoundResult, run_autotuning_loop
from epidemic_core.kg_qwen.plan_contract import deterministic_fallback_plan, validate_plan
from epidemic_core.kg_r3c.execution import TrainingRow

TRACK_KEY = "SYN:demo:synthetic_cases:month"
FREQUENCY = "month"
TRAIN_N = 168
VAL_N = 36
TEST_N = 36
N_ROWS = TRAIN_N + VAL_N + TEST_N

PATTERN = [30.0, 25.0, 20.0, 15.0, 12.0, 10.0, 12.0, 15.0, 20.0, 28.0, 35.0, 40.0]


def make_rows(periodic: bool = True) -> list[TrainingRow]:
    """Synthetic monthly series; `periodic=True` is exactly 12-periodic so the
    seasonal-naive baseline is unbeatable (WAPE 0)."""
    start = date(2005, 1, 1)
    rows: list[TrainingRow] = []
    for i in range(N_ROWS):
        value = PATTERN[i % 12] if periodic else PATTERN[i % 12] + (i // 12) * 1.5
        rows.append(TrainingRow(period_start=period_add(start, FREQUENCY, i), period_end=None, value=value))
    return rows


@pytest.fixture(scope="module")
def rows() -> list[TrainingRow]:
    return make_rows(periodic=True)


@pytest.fixture(scope="module")
def split(rows: list[TrainingRow]) -> TemporalSplit:
    return make_split([r.period_start for r in rows], TRAIN_N, VAL_N, TEST_N)


@pytest.fixture(scope="module")
def snapshot_sha(rows: list[TrainingRow]) -> str:
    return compute_snapshot_sha256(rows)


def make_tool_context(rows: list[TrainingRow], split: TemporalSplit, snapshot_sha: str) -> ToolContext:
    return ToolContext(
        track_key=TRACK_KEY,
        frequency=FREQUENCY,
        trainval_rows=rows[: TRAIN_N + VAL_N],
        snapshot_sha256=snapshot_sha,
        split=split,
        validation_summaries=[],
    )


def legal_plan(
    snapshot_sha: str,
    *,
    candidates: tuple[str, ...] = ("seasonal_negbin_glm.v1", "fourier_negbin_glm.v1"),
    max_trials: int = 8,
) -> dict[str, Any]:
    """A plan derived from the deterministic fallback, narrowed to the given
    candidates/trial budget; passes validate_plan by construction."""
    plan = deterministic_fallback_plan(snapshot_sha, FREQUENCY, "unit test planner")
    plan["candidate_models"] = list(candidates)
    plan["max_trials_per_round"] = max_trials
    plan["parameter_spaces"] = [e for e in plan["parameter_spaces"] if e["adapter_id"] in candidates]
    assert validate_plan(plan, expected_snapshot_sha256=snapshot_sha, frequency=FREQUENCY) == []
    return plan


def qwen_proposal(plan: dict[str, Any]) -> QwenProposal:
    return QwenProposal(plan=plan, plan_source="QWEN", prompt_sha256="p" * 64, response_sha256="r" * 64, latency_ms=7)


def static_planner(plan: dict[str, Any]) -> Callable[[dict[str, Any], int], QwenProposal]:
    calls: list[int] = []

    def planner(context: dict[str, Any], round_index: int) -> QwenProposal:
        calls.append(round_index)
        return qwen_proposal(plan)

    planner.calls = calls  # type: ignore[attr-defined]
    return planner


def run_loop(
    rows: list[TrainingRow],
    split: TemporalSplit,
    snapshot_sha: str,
    planner: Callable[[dict[str, Any], int], QwenProposal],
    **overrides: Any,
) -> tuple[LoopResult, list[RoundResult], ToolContext]:
    ctx = make_tool_context(rows, split, snapshot_sha)
    completed: list[RoundResult] = []
    kwargs: dict[str, Any] = dict(
        track_key=TRACK_KEY,
        frequency=FREQUENCY,
        trainval_rows=ctx.trainval_rows,
        split=split,
        snapshot_sha256=snapshot_sha,
        planner=planner,
        tool_context=ctx,
        on_round_complete=completed.append,
    )
    kwargs.update(overrides)
    return run_autotuning_loop(**kwargs), completed, ctx


def candidate_trials(round_result: RoundResult) -> list[Any]:
    """Round trials minus the four fixed baseline trials."""
    return round_result.trials[4:]


# --------------------------------------------------------------------- tests
def test_multi_round_execution_and_callback_count(
    rows: list[TrainingRow], split: TemporalSplit, snapshot_sha: str
) -> None:
    plan = legal_plan(snapshot_sha, max_trials=8)
    planner = static_planner(plan)
    result, completed, ctx = run_loop(rows, split, snapshot_sha, planner)

    # The exactly-periodic series gives the seasonal-naive baseline WAPE 0, so
    # no tuned model can improve by >= 1%: the loop must exhaust its budget.
    assert result.stop_reason == "BUDGET_EXHAUSTED"
    assert len(result.rounds) == 3
    assert len(completed) == 3  # checkpoint callback once per round
    assert [r.round_index for r in result.rounds] == [1, 2, 3]
    assert all(r.validation_status == "ACCEPTED" for r in result.rounds)
    assert all(r.proposal.plan_source == "QWEN" for r in result.rounds)
    # Round history is fed forward into the tool context.
    assert len(ctx.validation_summaries) == 3
    assert [s["round_index"] for s in ctx.validation_summaries] == [1, 2, 3]
    assert result.baseline_metrics.keys() == {"last_value", "rolling_mean", "drift", "seasonal_naive"}


def test_budget_cap_clamps_plan_trials(rows: list[TrainingRow], split: TemporalSplit, snapshot_sha: str) -> None:
    # Plan asks for the SPEC maximum with three candidates -> share 16 each.
    plan = legal_plan(
        snapshot_sha,
        candidates=("negbin_dglm.v1", "seasonal_negbin_glm.v1", "fourier_negbin_glm.v1"),
        max_trials=50,
    )
    result, _, _ = run_loop(rows, split, snapshot_sha, static_planner(plan), max_rounds=1)
    assert len(result.rounds) == 1
    assert len(candidate_trials(result.rounds[0])) == 48  # 3 adapters x 16, clamped to <= 50
    assert result.rounds[0].trials_executed == 48 + 4  # + baselines in round 1

    # A tiny loop-level cap clamps harder: share floors at 4 per candidate.
    plan_small = legal_plan(snapshot_sha, max_trials=50)
    result2, _, _ = run_loop(
        rows, split, snapshot_sha, static_planner(plan_small), max_rounds=1, max_trials_per_round=6
    )
    assert len(candidate_trials(result2.rounds[0])) == 8  # 2 adapters x max(4, 6//2)


def test_fallback_after_rejection_records_both_attempts(
    rows: list[TrainingRow], split: TemporalSplit, snapshot_sha: str
) -> None:
    bad_plan = legal_plan(snapshot_sha, max_trials=4)
    bad_plan["dataset_snapshot_sha256"] = "0" * 64  # snapshot mismatch -> rejected
    planner = static_planner(bad_plan)
    result, _, _ = run_loop(rows, split, snapshot_sha, planner, max_rounds=1)

    round1 = result.rounds[0]
    assert planner.calls == [1, 1]  # initial attempt + exactly one retry
    assert round1.validation_status == "FALLBACK_AFTER_REJECTION"
    assert round1.proposal.plan_source == "QWEN_FALLBACK_DETERMINISTIC"
    attempts = {e["attempt"] for e in round1.validation_errors}
    assert attempts == {"1", "2"}  # both rounds of errors are preserved
    assert any(e["code"] == "PLAN_SNAPSHOT_MISMATCH" for e in round1.validation_errors)
    # The loop still executed with the deterministic fallback plan.
    assert len(candidate_trials(round1)) > 0


def test_planner_exception_degrades_to_fallback(
    rows: list[TrainingRow], split: TemporalSplit, snapshot_sha: str
) -> None:
    def broken_planner(context: dict[str, Any], round_index: int) -> QwenProposal:
        raise RuntimeError("synthetic qwen outage")

    result, _, _ = run_loop(rows, split, snapshot_sha, broken_planner, max_rounds=1)
    round1 = result.rounds[0]
    assert round1.proposal.plan_source == "QWEN_FALLBACK_DETERMINISTIC"
    assert round1.proposal.prompt_sha256 is None
    assert round1.validation_status == "ACCEPTED"  # fallback plan itself validates clean
    assert len(candidate_trials(round1)) > 0


def test_target_met_stops_after_first_round(
    split: TemporalSplit, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Relax every target threshold so round 1 must qualify. Use the trended
    # (non-periodic) series so the baseline WAPE is positive and the
    # improvement ratio is defined.
    trended_rows = make_rows(periodic=False)
    trended_sha = compute_snapshot_sha256(trended_rows)
    monkeypatch.setattr(kq_loop, "TARGET_WAPE", 10.0)
    monkeypatch.setattr(kq_loop, "TARGET_AGREEMENT_PCT", 0.0)
    monkeypatch.setattr(kq_loop, "COVERAGE_MIN", 0.0)
    monkeypatch.setattr(kq_loop, "BASELINE_IMPROVEMENT_MIN", -100.0)
    plan = legal_plan(trended_sha, max_trials=4)
    result, completed, _ = run_loop(trended_rows, split, trended_sha, static_planner(plan))

    assert len(result.rounds) == 1
    assert len(completed) == 1
    assert result.rounds[0].target_met is True
    assert result.rounds[0].stop_reason == "TARGET_MET"
    assert result.stop_reason == "TARGET_MET"
    assert result.target_met is True
    assert result.champion_source == "TUNED_MODEL"
    assert result.champion_adapter_id in plan["candidate_models"]
    assert result.champion_params  # tuned champion carries the winning params


def test_baseline_stays_champion_when_not_beaten(
    rows: list[TrainingRow], split: TemporalSplit, snapshot_sha: str
) -> None:
    # Exactly-periodic data: seasonal_naive has WAPE 0 and cannot be improved
    # by >= 1%, so the champion must honestly remain the baseline.
    plan = legal_plan(snapshot_sha, max_trials=6)
    result, _, _ = run_loop(rows, split, snapshot_sha, static_planner(plan))

    assert result.champion_source == "BASELINE"
    assert result.champion_adapter_id == "seasonal_naive"
    assert result.champion_params == {"season_lag": 12}
    assert result.baseline_metrics["seasonal_naive"]["common_wape"] == 0.0
    assert result.best_val_wape == 0.0
    assert result.target_met is False


def test_loop_is_deterministic(rows: list[TrainingRow], split: TemporalSplit, snapshot_sha: str) -> None:
    plan = legal_plan(snapshot_sha, max_trials=8)
    result_a, _, _ = run_loop(rows, split, snapshot_sha, static_planner(plan))
    result_b, _, _ = run_loop(rows, split, snapshot_sha, static_planner(plan))

    assert result_a.champion_adapter_id == result_b.champion_adapter_id
    assert result_a.champion_params == result_b.champion_params
    assert result_a.champion_source == result_b.champion_source
    assert result_a.stop_reason == result_b.stop_reason
    shas_a = [[t.trial_sha256 for t in r.trials] for r in result_a.rounds]
    shas_b = [[t.trial_sha256 for t in r.trials] for r in result_b.rounds]
    assert shas_a == shas_b
    best_a = [r.best_trial_sha256 for r in result_a.rounds]
    best_b = [r.best_trial_sha256 for r in result_b.rounds]
    assert best_a == best_b


def test_planner_can_narrow_search_from_validation_history(
    rows: list[TrainingRow], split: TemporalSplit, snapshot_sha: str
) -> None:
    """Second-round planner inspects the context history and narrows bounds."""
    seen_history_lengths: list[int] = []

    def adaptive_planner(context: dict[str, Any], round_index: int) -> QwenProposal:
        history = context.get("validation_history", [])
        seen_history_lengths.append(len(history))
        plan = legal_plan(snapshot_sha, max_trials=4)
        if history:
            # Narrow the seasonal GLM shape range based on prior rounds.
            for entry in plan["parameter_spaces"]:
                if entry["adapter_id"] == "seasonal_negbin_glm.v1" and entry["parameter_name"] == "shape":
                    entry["lower"] = 5.0
                    entry["upper"] = 50.0
        return qwen_proposal(plan)

    result, _, _ = run_loop(rows, split, snapshot_sha, adaptive_planner, max_rounds=2)
    assert len(result.rounds) == 2
    assert seen_history_lengths == [0, 1]  # round 2 saw round 1's summary
