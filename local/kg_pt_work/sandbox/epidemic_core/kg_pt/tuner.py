"""Bounded random-search tuning over parameter contracts on rolling origins.

The tuner is pure: it receives ONLY the train+validation slice (test rows are
structurally out of reach), fits via the execution adapter contract on
``rows[:origin_index+1]`` and scores on labeled validation cells. Trial 0 is
always the contract-default parameter set (the pre-tuning baseline); trials
1..N sample from a single ``random.Random(seed)`` stream. A failing trial is
recorded with its ``failure_reason`` and never aborts the run, so re-running
with the same seed and inputs reproduces byte-identical trial content hashes
(``elapsed_ms`` is wall-clock and excluded from every hash).
"""

from __future__ import annotations

import math
import random
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from epidemic_core.forecast_model_kg.dual_loop import period_add
from epidemic_core.kg_pt import (
    COVERAGE_FLOOR,
    DEMO_FREQUENCY,
    HORIZONS,
    MAX_SECONDS,
    MAX_TRIALS,
    MIN_VALID_FOLDS,
    PT_IMPL_VERSION,
    PT_SEED,
)
from epidemic_core.kg_pt.contracts import (
    ParameterContract,
    clip_params,
    default_params,
    parameter_source_map,
    sample_params,
)
from epidemic_core.kg_pt.metrics import cell_metrics, mase_denominator
from epidemic_core.kg_pt.split import TemporalSplit, build_origin_spec, tuning_origin_indices
from epidemic_core.kg_r3c.execution import (
    TrainingRow,
    fit_model,
    predict_model,
    sha256_json,
    train_sha,
)

OBJECTIVE_COMMON_WAPE = "COMMON_WAPE_VALIDATION_ORIGINS"


@dataclass
class OriginRecord:
    """One origin evaluation inside a trial (audit evidence)."""

    origin_index: int
    origin_period: date
    issue_period: date
    data_through: date
    feature_max_period: date
    label_periods: tuple[date, ...]
    train_row_count: int
    validation_row_count: int
    dataset_sha256: str  # train sha of THIS origin's slice only
    seed: int
    artifact_sha256: str
    n_labeled: int
    mase_denominator: float | None
    origin_sha256: str
    cells: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class TrialResult:
    trial_index: int
    params: dict[str, Any]
    params_sha256: str
    parameter_source: dict[str, str]
    origins: list[OriginRecord]
    per_horizon_metrics: dict[int, dict[str, Any]]
    common_wape: float | None
    coverage: float | None
    avg_interval_width: float | None
    complexity_penalty: float | None
    objective_value: float | None
    gates: dict[str, bool]
    gate_pass: bool
    failure_reason: str | None
    elapsed_ms: int
    artifact_sha256: str | None
    dataset_snapshot_sha256: str
    trial_sha256: str


@dataclass
class TuningResult:
    adapter_id: str
    seed: int
    max_trials: int
    max_seconds: int
    frequency: str
    objective: str
    code_version: str
    dataset_snapshot_sha256: str
    trials: list[TrialResult]
    best_trial_index: int | None
    stopped_early: bool
    status: str  # 'COMPLETED' | 'FAILED_ALL_TRIALS'


def complexity_penalty(contracts: Sequence[ParameterContract], params: dict[str, Any]) -> float:
    """0.001 * sum of per-parameter normalized deviation from the default."""
    total = 0.0
    for c in contracts:
        if not c.tunable or c.default_value is None or c.lower_bound is None or c.upper_bound is None:
            continue
        span = float(c.upper_bound) - float(c.lower_bound)
        if span <= 0:
            continue
        value = params.get(c.parameter_name, c.default_value)
        total += abs(float(value) - float(c.default_value)) / span
    return 0.001 * total


def select_best_trial(trials: Sequence[TrialResult]) -> int | None:
    """Best = minimum objective among gate-passing trials; ties break to lower
    complexity penalty, then lower trial index. None when no trial passes.

    This function only ever sees tuning trials (validation-origin scores); it
    has no access to test metrics by construction.
    """
    candidates = [t for t in trials if t.gate_pass and t.objective_value is not None]
    if not candidates:
        return None
    best = min(candidates, key=lambda t: (t.objective_value, t.complexity_penalty or 0.0, t.trial_index))
    return best.trial_index


def _origin_cells(
    rows: Sequence[TrainingRow], origin_index: int, origin_period: date, frequency: str, preds: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Attach labels to predictions; a series gap keeps the label missing."""
    cells: list[dict[str, Any]] = []
    for p in preds:
        horizon = int(p["horizon"])
        target_idx = origin_index + horizon
        target_period = period_add(origin_period, frequency, horizon)
        actual: float | None = None
        if target_idx < len(rows):
            label_row = rows[target_idx]
            if label_row.period_start == target_period:
                actual = label_row.value
        cells.append(
            {
                "origin_index": origin_index,
                "horizon": horizon,
                "target_period": target_period,
                "yhat": p.get("yhat"),
                "lower_bound": p.get("lower_bound"),
                "upper_bound": p.get("upper_bound"),
                "actual_value": actual,
            }
        )
    return cells


def _scaled_mase(cells: list[dict[str, Any]], denom_by_origin: dict[int, float | None]) -> float | None:
    """MASE as the mean of per-cell |error| / per-origin-train-slice denominator."""
    terms = []
    for c in cells:
        denom = denom_by_origin.get(c["origin_index"])
        if c["yhat"] is None or c["actual_value"] is None or not denom:
            continue
        terms.append(abs(float(c["yhat"]) - float(c["actual_value"])) / denom)
    if not terms:
        return None
    value = sum(terms) / len(terms)
    return value if math.isfinite(value) else None


def _run_trial(
    trial_index: int,
    params: dict[str, Any],
    contracts: Sequence[ParameterContract],
    rows: Sequence[TrainingRow],
    split: TemporalSplit,
    origin_indices: list[int],
    adapter_id: str,
    seed: int,
    frequency: str,
    dataset_snapshot_sha256: str,
    started: float,
) -> TrialResult:
    periods = [r.period_start for r in rows]
    label_window = (split.validation_start, split.validation_end)
    origins: list[OriginRecord] = []
    all_cells: list[dict[str, Any]] = []
    denom_by_origin: dict[int, float | None] = {}
    for origin_index in origin_indices:
        spec = build_origin_spec(periods, origin_index, frequency, label_window)
        train_rows = list(rows[: origin_index + 1])  # hard boundary: never past the origin
        fitted = fit_model(adapter_id, train_rows, params, seed, spec.origin_period, frequency)
        preds = predict_model(fitted, HORIZONS, spec.origin_period)
        denom = mase_denominator([r.value for r in train_rows])
        denom_by_origin[origin_index] = denom
        cells = _origin_cells(rows, origin_index, spec.origin_period, frequency, preds)
        all_cells.extend(cells)
        n_labeled = sum(1 for c in cells if c["yhat"] is not None and c["actual_value"] is not None)
        origin_sha = sha256_json(
            {
                "trial_index": trial_index,
                "origin_index": origin_index,
                "origin_period": spec.origin_period.isoformat(),
                "train_row_count": spec.train_row_count,
                "dataset_sha256": fitted.train_sha256,
                "artifact_sha256": fitted.artifact_sha256,
                "labels": [c["actual_value"] for c in cells],
                "yhat": [c["yhat"] for c in cells],
                "seed": seed,
            }
        )
        origins.append(
            OriginRecord(
                origin_index=origin_index,
                origin_period=spec.origin_period,
                issue_period=spec.issue_period,
                data_through=spec.data_through,
                feature_max_period=spec.feature_max_period,
                label_periods=spec.label_periods,
                train_row_count=spec.train_row_count,
                validation_row_count=spec.validation_row_count,
                dataset_sha256=fitted.train_sha256,
                seed=seed,
                artifact_sha256=fitted.artifact_sha256,
                n_labeled=n_labeled,
                mase_denominator=denom,
                origin_sha256=origin_sha,
                cells=cells,
            )
        )
    overall = cell_metrics(
        [(c["yhat"], c["actual_value"], c["lower_bound"], c["upper_bound"]) for c in all_cells],
        mase_denom=None,
    )
    overall["mase"] = _scaled_mase(all_cells, denom_by_origin)
    per_horizon: dict[int, dict[str, Any]] = {}
    for horizon in HORIZONS:
        hcells = [c for c in all_cells if c["horizon"] == horizon]
        hm = cell_metrics(
            [(c["yhat"], c["actual_value"], c["lower_bound"], c["upper_bound"]) for c in hcells],
            mase_denom=None,
        )
        hm["mase"] = _scaled_mase(hcells, denom_by_origin)
        per_horizon[horizon] = hm
    common_wape = overall["wape"]
    coverage = overall["coverage"]
    avg_width = overall["avg_width"]
    penalty = complexity_penalty(contracts, params)
    objective = common_wape * (1.0 + penalty) if common_wape is not None else None
    folds_with_labels = sum(1 for o in origins if o.n_labeled > 0)
    yhat_values = [c["yhat"] for c in all_cells if c["yhat"] is not None]
    gates = {
        "non_negative": all(float(y) >= 0 for y in yhat_values),
        "finite": all(math.isfinite(float(y)) for y in yhat_values)
        and all(
            v is None or math.isfinite(float(v))
            for v in (common_wape, coverage, avg_width, penalty, objective, overall["mase"])
        ),
        "min_folds": folds_with_labels >= MIN_VALID_FOLDS,
        "coverage_floor": coverage is not None and coverage >= COVERAGE_FLOOR,
    }
    gate_pass = all(gates.values()) and common_wape is not None
    artifact_sha = sha256_json([o.artifact_sha256 for o in origins])
    elapsed_ms = int((time.monotonic() - started) * 1000)
    trial_sha = _trial_sha256(
        trial_index,
        params,
        contracts,
        origins,
        per_horizon,
        common_wape,
        coverage,
        avg_width,
        penalty,
        objective,
        gates,
        gate_pass,
        None,
        artifact_sha,
        dataset_snapshot_sha256,
    )
    return TrialResult(
        trial_index=trial_index,
        params=params,
        params_sha256=sha256_json(params),
        parameter_source=parameter_source_map(list(contracts)),
        origins=origins,
        per_horizon_metrics=per_horizon,
        common_wape=common_wape,
        coverage=coverage,
        avg_interval_width=avg_width,
        complexity_penalty=penalty,
        objective_value=objective,
        gates=gates,
        gate_pass=gate_pass,
        failure_reason=None,
        elapsed_ms=elapsed_ms,
        artifact_sha256=artifact_sha,
        dataset_snapshot_sha256=dataset_snapshot_sha256,
        trial_sha256=trial_sha,
    )


def _trial_sha256(
    trial_index: int,
    params: dict[str, Any],
    contracts: Sequence[ParameterContract],
    origins: list[OriginRecord],
    per_horizon: dict[int, dict[str, Any]],
    common_wape: float | None,
    coverage: float | None,
    avg_width: float | None,
    penalty: float | None,
    objective: float | None,
    gates: dict[str, bool],
    gate_pass: bool,
    failure_reason: str | None,
    artifact_sha: str | None,
    dataset_snapshot_sha256: str,
) -> str:
    """Deterministic content hash of a trial (elapsed_ms deliberately excluded)."""
    return sha256_json(
        {
            "trial_index": trial_index,
            "params": params,
            "parameter_source": parameter_source_map(list(contracts)),
            "origin_sha256": [o.origin_sha256 for o in origins],
            "per_horizon_metrics": per_horizon,
            "common_wape": common_wape,
            "coverage": coverage,
            "avg_interval_width": avg_width,
            "complexity_penalty": penalty,
            "objective_value": objective,
            "gates": gates,
            "gate_pass": gate_pass,
            "failure_reason": failure_reason,
            "artifact_sha256": artifact_sha,
            "dataset_snapshot_sha256": dataset_snapshot_sha256,
            "code_version": PT_IMPL_VERSION,
        }
    )


def _failed_trial(
    trial_index: int,
    params: dict[str, Any],
    contracts: Sequence[ParameterContract],
    dataset_snapshot_sha256: str,
    failure_reason: str,
    started: float,
) -> TrialResult:
    gates = {"non_negative": False, "finite": False, "min_folds": False, "coverage_floor": False}
    trial_sha = _trial_sha256(
        trial_index,
        params,
        contracts,
        [],
        {},
        None,
        None,
        None,
        None,
        None,
        gates,
        False,
        failure_reason,
        None,
        dataset_snapshot_sha256,
    )
    return TrialResult(
        trial_index=trial_index,
        params=params,
        params_sha256=sha256_json(params),
        parameter_source=parameter_source_map(list(contracts)),
        origins=[],
        per_horizon_metrics={},
        common_wape=None,
        coverage=None,
        avg_interval_width=None,
        complexity_penalty=None,
        objective_value=None,
        gates=gates,
        gate_pass=False,
        failure_reason=failure_reason,
        elapsed_ms=int((time.monotonic() - started) * 1000),
        artifact_sha256=None,
        dataset_snapshot_sha256=dataset_snapshot_sha256,
        trial_sha256=trial_sha,
    )


def run_tuning(
    trainval_rows: Sequence[TrainingRow],
    split: TemporalSplit,
    adapter_id: str,
    contracts: Sequence[ParameterContract],
    seed: int = PT_SEED,
    max_trials: int = MAX_TRIALS,
    max_seconds: int = MAX_SECONDS,
    frequency: str = DEMO_FREQUENCY,
) -> TuningResult:
    """Run bounded tuning. ``trainval_rows`` must be exactly the
    train+validation slice; the tuner has no parameter that could carry test
    rows, and origin labels never leave the train+validation window."""
    started = time.monotonic()
    rng = random.Random(seed)
    contracts = list(contracts)
    origin_indices = tuning_origin_indices(len(trainval_rows), split)
    dataset_snapshot_sha256 = train_sha(list(trainval_rows))
    defaults = default_params(contracts)
    trials: list[TrialResult] = []
    stopped_early = False
    for trial_index in range(max_trials):
        if trial_index > 0 and (time.monotonic() - started) > max_seconds:
            stopped_early = True
            break
        params = dict(defaults) if trial_index == 0 else clip_params(contracts, sample_params(contracts, rng))
        trial_started = time.monotonic()
        try:
            trial = _run_trial(
                trial_index,
                params,
                contracts,
                trainval_rows,
                split,
                origin_indices,
                adapter_id,
                seed,
                frequency,
                dataset_snapshot_sha256,
                trial_started,
            )
        except Exception as exc:  # noqa: BLE001 - one bad trial must never kill the run
            trial = _failed_trial(
                trial_index, params, contracts, dataset_snapshot_sha256, f"{type(exc).__name__}:{exc}", trial_started
            )
        trials.append(trial)
    best = select_best_trial(trials)
    return TuningResult(
        adapter_id=adapter_id,
        seed=seed,
        max_trials=max_trials,
        max_seconds=max_seconds,
        frequency=frequency,
        objective=OBJECTIVE_COMMON_WAPE,
        code_version=PT_IMPL_VERSION,
        dataset_snapshot_sha256=dataset_snapshot_sha256,
        trials=trials,
        best_trial_index=best,
        stopped_early=stopped_early,
        status="COMPLETED" if best is not None else "FAILED_ALL_TRIALS",
    )
