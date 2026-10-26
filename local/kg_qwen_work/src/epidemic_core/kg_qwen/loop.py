"""Qwen multi-round autotuning loop (pure logic; DB access lives in pipeline).

Safety invariants enforced here:
  * the loop only ever receives the train+validation slice (``trainval_rows``);
    hidden-eval rows have no parameter that could carry them into this module;
  * the planner is an injected callable returning :class:`QwenProposal`; every
    plan is validated by ``plan_contract.validate_plan`` before execution, and
    invalid plans get exactly one planner retry (with the errors fed back via
    the context) before a deterministic fallback plan is used;
  * per-round trial budgets are clamped to ``MAX_TRIALS_PER_ROUND`` and the
    wall-clock budget is defensive-clamped into every ``run_tuning`` call;
  * baselines (``BASELINE_ADAPTER_IDS``) are evaluated once with the contract
    defaults (trial 0) and always participate in the comparison; a tuned model
    only becomes champion when it beats the best baseline by at least
    ``BASELINE_IMPROVEMENT_MIN`` relative common-WAPE, otherwise the champion
    honestly stays a baseline;
  * all randomness flows through ``seed``; re-running with the same seed and
    inputs reproduces identical content hashes (``elapsed_ms``/``latency_ms``
    are wall-clock and excluded from every hash).

This module is pure: no DB, no network, no subprocess.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from epidemic_core.kg_pt.metrics import agreement_pct
from epidemic_core.kg_pt.split import TemporalSplit
from epidemic_core.kg_pt.tuner import TrialResult, run_tuning, select_best_trial
from epidemic_core.kg_qwen import (
    BASELINE_IMPROVEMENT_MIN,
    COVERAGE_MIN,
    KQ_SEED,
    MAX_ROUNDS,
    MAX_TOTAL_MINUTES,
    MAX_TRIALS_PER_ROUND,
    TARGET_AGREEMENT_PCT,
    TARGET_WAPE,
    adapters_ext,
    agent_tools,
    plan_contract,
)
from epidemic_core.kg_qwen.agent_tools import ToolContext
from epidemic_core.kg_qwen.contracts_ext import BASELINE_ADAPTER_IDS, all_contracts_for
from epidemic_core.kg_r3c.execution import TrainingRow

# Candidate adapters never receive fewer trials than this, even when the
# per-round budget divided by the candidate count would give less.
MIN_TRIALS_PER_CANDIDATE = 4

STOP_TARGET_MET = "TARGET_MET"
STOP_BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"

VALIDATION_ACCEPTED = "ACCEPTED"
VALIDATION_FALLBACK_AFTER_REJECTION = "FALLBACK_AFTER_REJECTION"

PLAN_SOURCE_QWEN = "QWEN"
PLAN_SOURCE_FALLBACK = "QWEN_FALLBACK_DETERMINISTIC"

CHAMPION_TUNED = "TUNED_MODEL"
CHAMPION_BASELINE = "BASELINE"


@dataclass
class QwenProposal:
    """One planner output for one round (plan plus provenance hashes)."""

    plan: dict[str, Any]
    plan_source: str  # 'QWEN' | 'QWEN_FALLBACK_DETERMINISTIC'
    prompt_sha256: str | None
    response_sha256: str | None
    latency_ms: int | None


@dataclass
class RoundResult:
    """Everything auditable about one loop round.

    ``trials`` contains this round's candidate trials plus the baseline
    evaluation trials (baselines are executed once, with the loop seed, and
    the same deterministic trial objects are attached to every round for
    audit completeness). ``trials_executed`` counts trials actually executed
    in this round (candidate trials, plus the baseline trials in round 1).
    """

    round_index: int
    proposal: QwenProposal
    validation_status: str  # 'ACCEPTED' | 'FALLBACK_AFTER_REJECTION'
    validation_errors: list[dict[str, str]]
    trials: list[TrialResult]  # 该轮全部 trials（含基线评估 trial）
    best_adapter_id: str | None
    best_trial_sha256: str | None
    best_val_wape: float | None
    best_val_agreement_pct: float | None
    best_val_coverage: float | None
    baseline_best_wape: float | None
    improvement_vs_baseline: float | None
    target_met: bool
    stop_reason: str | None
    trials_executed: int
    # Parallel to ``trials`` (same order, same length): TrialResult carries no
    # adapter field, so the owning adapter_id rides alongside for persistence.
    trial_adapter_ids: list[str] = field(default_factory=list)


@dataclass
class LoopResult:
    """Final outcome of the autotuning loop (JSON-friendly scalar fields)."""

    track_key: str
    rounds: list[RoundResult] = field(default_factory=list)
    champion_adapter_id: str = ""
    champion_params: dict[str, Any] = field(default_factory=dict)
    champion_source: str = CHAMPION_BASELINE  # 'TUNED_MODEL' | 'BASELINE'
    target_met: bool = False
    stop_reason: str = STOP_BUDGET_EXHAUSTED  # 'TARGET_MET' | 'BUDGET_EXHAUSTED'
    best_val_wape: float | None = None
    best_val_agreement_pct: float | None = None
    best_val_coverage: float | None = None
    baseline_metrics: dict[str, dict[str, Any]] = field(default_factory=dict)


def _errors_as_dicts(errors: Sequence[plan_contract.PlanError], attempt: int) -> list[dict[str, str]]:
    return [{"code": e.code, "message": e.message, "path": e.path, "attempt": str(attempt)} for e in errors]


def _propose_validated_plan(
    *,
    planner: Callable[[dict[str, Any], int], QwenProposal],
    context: dict[str, Any],
    round_index: int,
    snapshot_sha256: str,
    frequency: str,
) -> tuple[QwenProposal, str, list[dict[str, str]]]:
    """Planner call + validation with exactly one error-feedback retry.

    Returns (proposal, validation_status, validation_errors). A planner
    exception degrades to the deterministic fallback plan with
    ``plan_source='QWEN_FALLBACK_DETERMINISTIC'``; a plan that still fails
    validation after the retry is replaced by the fallback plan and recorded
    as ``FALLBACK_AFTER_REJECTION`` with both attempts' errors preserved.
    """
    try:
        proposal = planner(context, round_index)
    except Exception as exc:  # noqa: BLE001 - a broken planner must never kill the loop
        fallback = plan_contract.deterministic_fallback_plan(
            snapshot_sha256, frequency, f"PLANNER_EXCEPTION:{type(exc).__name__}:{exc}"
        )
        proposal = QwenProposal(
            plan=fallback,
            plan_source=PLAN_SOURCE_FALLBACK,
            prompt_sha256=None,
            response_sha256=None,
            latency_ms=None,
        )
        errors = plan_contract.validate_plan(
            proposal.plan, expected_snapshot_sha256=snapshot_sha256, frequency=frequency
        )
        # The fallback plan is expected to validate clean; if it does not, the
        # errors are surfaced verbatim rather than silently accepted.
        status = VALIDATION_ACCEPTED if not errors else VALIDATION_FALLBACK_AFTER_REJECTION
        return proposal, status, _errors_as_dicts(errors, 1)

    errors = plan_contract.validate_plan(proposal.plan, expected_snapshot_sha256=snapshot_sha256, frequency=frequency)
    if not errors:
        return proposal, VALIDATION_ACCEPTED, []

    first_errors = _errors_as_dicts(errors, 1)
    # Feed the rejection back to the planner exactly once.
    retry_context = dict(context)
    retry_context["prior_validation_errors"] = [{"code": e.code, "message": e.message, "path": e.path} for e in errors]
    try:
        retry = planner(retry_context, round_index)
    except Exception as exc:  # noqa: BLE001 - retry failure falls through to fallback
        retry = QwenProposal(
            plan={},
            plan_source=PLAN_SOURCE_FALLBACK,
            prompt_sha256=None,
            response_sha256=None,
            latency_ms=None,
        )
        retry_errors = [
            plan_contract.PlanError(
                code="PLANNER_EXCEPTION",
                message=f"{type(exc).__name__}:{exc}",
                path="planner",
            )
        ]
    else:
        retry_errors = plan_contract.validate_plan(
            retry.plan, expected_snapshot_sha256=snapshot_sha256, frequency=frequency
        )
    if not retry_errors:
        return retry, VALIDATION_ACCEPTED, []

    fallback = plan_contract.deterministic_fallback_plan(
        snapshot_sha256, frequency, "PLAN_REJECTED_AFTER_RETRY"
    )
    proposal = QwenProposal(
        plan=fallback,
        plan_source=PLAN_SOURCE_FALLBACK,
        prompt_sha256=retry.prompt_sha256,
        response_sha256=retry.response_sha256,
        latency_ms=retry.latency_ms,
    )
    return (
        proposal,
        VALIDATION_FALLBACK_AFTER_REJECTION,
        first_errors + _errors_as_dicts(retry_errors, 2),
    )


def _evaluate_baselines(
    *,
    trainval_rows: Sequence[TrainingRow],
    split: TemporalSplit,
    frequency: str,
    seed: int,
    max_seconds: int,
) -> tuple[list[TrialResult], float | None, dict[str, dict[str, Any]]]:
    """One default-parameter trial per baseline adapter (max_trials=1).

    Empty contracts mean trial 0 is exactly the implementation defaults.
    Returns (trials, best gate-passed common WAPE, per-adapter metrics).
    """
    trials: list[TrialResult] = []
    metrics: dict[str, dict[str, Any]] = {}
    best_wape: float | None = None
    for adapter_id in BASELINE_ADAPTER_IDS:
        result = run_tuning(
            trainval_rows,
            split,
            adapter_id,
            [],
            seed=seed,
            max_trials=1,
            max_seconds=max_seconds,
            frequency=frequency,
        )
        trial = result.trials[0]
        trials.append(trial)
        agreement = agreement_pct(trial.common_wape)
        metrics[adapter_id] = {
            "common_wape": trial.common_wape,
            "agreement_pct": agreement,
            "coverage": trial.coverage,
            "gate_pass": trial.gate_pass,
            "trial_sha256": trial.trial_sha256,
            "failure_reason": trial.failure_reason,
        }
        if trial.gate_pass and trial.common_wape is not None and (best_wape is None or trial.common_wape < best_wape):
            best_wape = trial.common_wape
    return trials, best_wape, metrics


def _baseline_default_params(adapter_id: str, frequency: str) -> dict[str, Any]:
    """Honest default-parameter record for a baseline champion.

    Only ``seasonal_naive`` takes an explicit parameter (its lag rule follows
    the data frequency); the other baselines have no contract parameters and
    their in-code defaults are represented by an empty bundle.
    """
    if adapter_id == "seasonal_naive":
        return {"season_lag": 12 if frequency == "month" else 52}
    return {}


def _target_met(
    *,
    best_wape: float | None,
    best_agreement: float | None,
    best_coverage: float | None,
    improvement: float | None,
) -> bool:
    """Target = WAPE + agreement + coverage gate + honest baseline improvement."""
    return (
        best_wape is not None
        and best_wape <= TARGET_WAPE
        and best_agreement is not None
        and best_agreement >= TARGET_AGREEMENT_PCT
        and best_coverage is not None
        and best_coverage >= COVERAGE_MIN
        and improvement is not None
        and improvement >= BASELINE_IMPROVEMENT_MIN
    )


def _improvement(best_wape: float | None, baseline_best_wape: float | None) -> float | None:
    if best_wape is None or baseline_best_wape is None or baseline_best_wape <= 0:
        return None
    return (baseline_best_wape - best_wape) / baseline_best_wape


def run_autotuning_loop(
    *,
    track_key: str,
    frequency: str,
    trainval_rows: Sequence[TrainingRow],
    split: TemporalSplit,
    snapshot_sha256: str,
    planner: Callable[[dict[str, Any], int], QwenProposal],
    tool_context: ToolContext,
    on_round_complete: Callable[[RoundResult], None] | None = None,
    seed: int = KQ_SEED,
    max_rounds: int = MAX_ROUNDS,
    max_trials_per_round: int = MAX_TRIALS_PER_ROUND,
    max_total_seconds: int = MAX_TOTAL_MINUTES * 60,
) -> LoopResult:
    """Run the bounded multi-round plan -> tune -> summarize loop.

    Round flow (round_index = 1..max_rounds): whitelist context -> planner
    proposal (with one validation-feedback retry and deterministic fallback)
    -> plan-narrowed contracts -> bounded ``run_tuning`` per candidate adapter
    -> round summary (best candidate vs fixed baselines) -> checkpoint
    callback. Stops with ``TARGET_MET`` as soon as a round meets the target,
    otherwise ``BUDGET_EXHAUSTED`` when rounds or wall-clock budget run out.
    """
    started = time.monotonic()

    def remaining_seconds() -> int:
        return max(1, int(max_total_seconds - (time.monotonic() - started)))

    # New candidate adapters are registered before any fit dispatch (idempotent).
    adapters_ext.register_extended_models()

    # Baselines never change across rounds: evaluate once with the loop seed.
    baseline_trials, baseline_best_wape, baseline_metrics = _evaluate_baselines(
        trainval_rows=trainval_rows,
        split=split,
        frequency=frequency,
        seed=seed,
        max_seconds=remaining_seconds(),
    )

    result = LoopResult(track_key=track_key, baseline_metrics=baseline_metrics)
    # (adapter_id, trial) pairs: TrialResult carries no adapter field, so the
    # loop tracks ownership explicitly at the point each tuning run returns.
    all_candidate: list[tuple[str, TrialResult]] = []

    for round_index in range(1, max_rounds + 1):
        if time.monotonic() - started > max_total_seconds:
            result.stop_reason = STOP_BUDGET_EXHAUSTED
            break

        context = agent_tools.build_qwen_context(tool_context, round_index)
        proposal, validation_status, validation_errors = _propose_validated_plan(
            planner=planner,
            context=context,
            round_index=round_index,
            snapshot_sha256=snapshot_sha256,
            frequency=frequency,
        )
        plan = proposal.plan
        narrowed = plan_contract.narrowed_contracts(plan, frequency)

        candidate_ids = [str(a) for a in plan.get("candidate_models", [])]
        plan_budget = int(plan.get("max_trials_per_round", max_trials_per_round))
        round_budget = max(1, min(plan_budget, MAX_TRIALS_PER_ROUND, max_trials_per_round))
        share = max(MIN_TRIALS_PER_CANDIDATE, round_budget // len(candidate_ids)) if candidate_ids else 0

        round_candidate: list[tuple[str, TrialResult]] = []
        for adapter_id in candidate_ids:
            contracts = narrowed.get(adapter_id)
            if contracts is None:
                contracts = all_contracts_for(adapter_id, frequency)
            tuning = run_tuning(
                trainval_rows,
                split,
                adapter_id,
                contracts,
                seed=seed + round_index,
                max_trials=share,
                max_seconds=remaining_seconds(),
                frequency=frequency,
            )
            round_candidate.extend((adapter_id, t) for t in tuning.trials)

        # Round best = minimum objective among gate-passed per-adapter winners.
        best_pair: tuple[str, TrialResult] | None = None
        for adapter_id in candidate_ids:
            adapter_trials = [t for aid, t in round_candidate if aid == adapter_id]
            best_index = select_best_trial(adapter_trials)
            if best_index is None:
                continue
            winner = next(t for t in adapter_trials if t.trial_index == best_index)
            if best_pair is None or (winner.objective_value or 0.0) < (best_pair[1].objective_value or 0.0):
                best_pair = (adapter_id, winner)
        all_candidate.extend(round_candidate)

        best_trial = best_pair[1] if best_pair is not None else None
        best_wape = best_trial.common_wape if best_trial is not None else None
        best_agreement = agreement_pct(best_wape)
        best_coverage = best_trial.coverage if best_trial is not None else None
        improvement = _improvement(best_wape, baseline_best_wape)
        target_met = _target_met(
            best_wape=best_wape,
            best_agreement=best_agreement,
            best_coverage=best_coverage,
            improvement=improvement,
        )
        stop_reason: str | None = None
        if target_met:
            stop_reason = STOP_TARGET_MET
        elif round_index == max_rounds or (time.monotonic() - started) > max_total_seconds:
            stop_reason = STOP_BUDGET_EXHAUSTED

        round_result = RoundResult(
            round_index=round_index,
            proposal=proposal,
            validation_status=validation_status,
            validation_errors=validation_errors,
            trials=list(baseline_trials) + [t for _, t in round_candidate],
            trial_adapter_ids=list(BASELINE_ADAPTER_IDS) + [aid for aid, _ in round_candidate],
            best_adapter_id=None if best_pair is None else best_pair[0],
            best_trial_sha256=None if best_trial is None else best_trial.trial_sha256,
            best_val_wape=best_wape,
            best_val_agreement_pct=best_agreement,
            best_val_coverage=best_coverage,
            baseline_best_wape=baseline_best_wape,
            improvement_vs_baseline=improvement,
            target_met=target_met,
            stop_reason=stop_reason,
            trials_executed=len(round_candidate) + (len(baseline_trials) if round_index == 1 else 0),
        )
        result.rounds.append(round_result)

        # Make this round's aggregated validation summary visible to the next
        # round's context (aggregates only, never per-row labels).
        tool_context.validation_summaries.append(
            {
                "round_index": round_index,
                "best_adapter_id": round_result.best_adapter_id,
                "best_val_wape": best_wape,
                "best_val_agreement_pct": best_agreement,
                "best_val_coverage": best_coverage,
                "baseline_best_wape": baseline_best_wape,
                "improvement_vs_baseline": improvement,
                "target_met": target_met,
                "trials_executed": round_result.trials_executed,
            }
        )

        if on_round_complete is not None:
            on_round_complete(round_result)

        if stop_reason is not None:
            result.stop_reason = stop_reason
            break
    else:
        result.stop_reason = STOP_BUDGET_EXHAUSTED

    _select_champion(
        result,
        candidate_trials=all_candidate,
        baseline_best_wape=baseline_best_wape,
        baseline_metrics=baseline_metrics,
        frequency=frequency,
    )
    result.target_met = any(r.target_met for r in result.rounds)
    if result.target_met:
        result.stop_reason = STOP_TARGET_MET
    return result


def _select_champion(
    result: LoopResult,
    *,
    candidate_trials: list[tuple[str, TrialResult]],
    baseline_best_wape: float | None,
    baseline_metrics: dict[str, dict[str, Any]],
    frequency: str,
) -> None:
    """Champion = best cross-round gate-passed trial, unless it fails to beat
    the best baseline by >= BASELINE_IMPROVEMENT_MIN, in which case the best
    baseline honestly stays champion."""
    best_trial: TrialResult | None = None
    best_adapter: str | None = None
    for adapter_id, trial in candidate_trials:
        if not trial.gate_pass or trial.objective_value is None:
            continue
        if best_trial is None or trial.objective_value < (best_trial.objective_value or 0.0):
            best_trial = trial
            best_adapter = adapter_id

    tuned_wape = best_trial.common_wape if best_trial is not None else None
    improvement = _improvement(tuned_wape, baseline_best_wape)
    tuned_is_champion = (
        best_trial is not None and improvement is not None and improvement >= BASELINE_IMPROVEMENT_MIN
    )

    if tuned_is_champion and best_trial is not None and best_adapter is not None:
        result.champion_adapter_id = best_adapter
        result.champion_params = dict(best_trial.params)
        result.champion_source = CHAMPION_TUNED
        result.best_val_wape = best_trial.common_wape
        result.best_val_agreement_pct = agreement_pct(best_trial.common_wape)
        result.best_val_coverage = best_trial.coverage
        return

    # Baseline stays champion: pick the gate-passed baseline with the lowest
    # common WAPE; fall back to the lowest-WAPE baseline regardless of gates,
    # then to seasonal_naive, so the field is never silently empty.
    baseline_ids = [
        aid
        for aid, m in baseline_metrics.items()
        if m.get("gate_pass") and m.get("common_wape") is not None
    ]
    if not baseline_ids:
        baseline_ids = [aid for aid, m in baseline_metrics.items() if m.get("common_wape") is not None]
    if baseline_ids:
        champion = min(baseline_ids, key=lambda aid: float(baseline_metrics[aid]["common_wape"]))
    else:
        champion = "seasonal_naive"
    result.champion_adapter_id = champion
    result.champion_params = _baseline_default_params(champion, frequency)
    result.champion_source = CHAMPION_BASELINE
    metrics = baseline_metrics.get(champion, {})
    wape = metrics.get("common_wape")
    result.best_val_wape = wape
    result.best_val_agreement_pct = agreement_pct(wape)
    result.best_val_coverage = metrics.get("coverage")
