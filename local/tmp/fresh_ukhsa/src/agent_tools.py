"""forecast-tuning-agent tool layer (SPEC section 3.6).

Safety invariants:

  * ``dispatch_tool`` only ever runs the six ``ALLOWED_TOOLS``; every other
    tool name — including the explicitly listed dangerous ones (exec, shell,
    file access, postgres, web, approval, promotion, active-pointer) — is
    rejected with ``TOOL_NOT_PERMITTED``;
  * every tool reads only ``ToolContext`` (pure data, no DB, no network, no
    filesystem): the dataset profile is computed from ``trainval_rows`` only,
    so test/hidden-eval labels are structurally absent;
  * ``build_qwen_context`` emits exactly the whitelisted key set — the
    qwen-context sentinel depends on this closed set;
  * ``dataset_profile`` is a pure function of its arguments.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any

from epidemic_core.kg_pt.split import TemporalSplit
from epidemic_core.kg_qwen import (
    BASELINE_IMPROVEMENT_MIN,
    COVERAGE_MIN,
    MAX_ROUNDS,
    MAX_TOTAL_MINUTES,
    MAX_TRIALS_PER_ROUND,
    TARGET_AGREEMENT_PCT,
    TARGET_WAPE,
)
from epidemic_core.kg_qwen.contracts_ext import (
    WHITELIST_TUNABLE_ADAPTERS,
    adapter_registry_payload,
    adapter_registry_slim,
    all_contracts_for,
    registered_adapters,
)
from epidemic_core.kg_qwen.plan_contract import PLAN_VALIDATOR_VERSION, validate_plan
from epidemic_core.kg_r3c.execution import TrainingRow

ALLOWED_TOOLS: tuple[str, ...] = (
    "get_dataset_profile",
    "get_kg_model_candidates",
    "get_parameter_contracts",
    "get_experiment_history",
    "get_validation_summary",
    "submit_tuning_plan",
)

# Explicitly blocked names, rejected via the same TOOL_NOT_PERMITTED path as
# any other unregistered tool. Listed for auditability and test coverage.
FORBIDDEN_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "exec",
        "shell",
        "process",
        "read_file",
        "write_file",
        "postgres",
        "web_search",
        "web_fetch",
        "gateway",
        "cron",
        "approval",
        "promotion",
        "set_active_pointer",
    }
)

# The exact key set of build_qwen_context output. The qwen-context sentinel
# relies on this being a closed whitelist: anything not listed here (notably
# hidden-eval labels or metrics) can never reach the planner.
CONTEXT_KEYS: frozenset[str] = frozenset(
    {
        "track_key",
        "frequency",
        "snapshot_sha256",
        "round_index",
        "dataset_profile",
        "candidates",
        "budgets",
        "targets",
        "validation_history",
    }
)


@dataclass
class ToolContext:
    """Pure data bundle handed to the agent tools; no database handles."""

    track_key: str
    frequency: str
    trainval_rows: Sequence[TrainingRow]
    snapshot_sha256: str
    split: TemporalSplit
    validation_summaries: list[dict[str, Any]]  # aggregate summaries of finished rounds


def dataset_profile(rows: Sequence[TrainingRow], frequency: str) -> dict[str, Any]:
    """Descriptive statistics of the train+validation slice (pure function).

    Missing observations are counted, never filled. The seasonal lag is 12
    for monthly series and 52 otherwise (weekly). The seasonal autocorrelation
    is the Pearson correlation over observed (t, t-lag) pairs only. The trend
    ratio compares the mean of the most recent 24 observed values against the
    mean of the 24 observed values before them.
    """
    n_obs = len(rows)
    values = [r.value for r in rows]
    observed = [float(v) for v in values if v is not None]
    n_observed = len(observed)
    missing_count = n_obs - n_observed

    mean: float | None = None
    variance: float | None = None
    overdispersion_ratio: float | None = None
    zero_ratio: float | None = None
    if n_observed:
        mean = sum(observed) / n_observed
        variance = sum((v - mean) ** 2 for v in observed) / n_observed
        overdispersion_ratio = (variance / mean) if mean > 0 else None
        zero_ratio = sum(1 for v in observed if v == 0) / n_observed

    seasonal_lag = 12 if frequency == "month" else 52
    seasonal_autocorrelation = _lag_autocorrelation(values, seasonal_lag)
    trend_ratio = _trend_ratio(observed, recent=24)

    return {
        "n_obs": n_obs,
        "first_period": rows[0].period_start.isoformat() if rows else None,
        "last_period": rows[-1].period_start.isoformat() if rows else None,
        "missing_count": missing_count,
        "missing_ratio": (missing_count / n_obs) if n_obs else None,
        "zero_ratio": zero_ratio,
        "mean": mean,
        "variance": variance,
        "overdispersion_ratio": overdispersion_ratio,
        "seasonal_lag": seasonal_lag,
        "seasonal_autocorrelation": seasonal_autocorrelation,
        "trend_ratio": trend_ratio,
    }


def _lag_autocorrelation(values: Sequence[float | None], lag: int) -> float | None:
    """Pearson correlation of observed (x[t], x[t-lag]) pairs; None if unstable."""
    xs: list[float] = []
    ys: list[float] = []
    for t in range(lag, len(values)):
        current = values[t]
        previous = values[t - lag]
        if current is not None and previous is not None:
            xs.append(float(previous))
            ys.append(float(current))
    n = len(xs)
    if n < 3:
        return None
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    sxx = sum((x - mean_x) ** 2 for x in xs)
    syy = sum((y - mean_y) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=False))
    return sxy / math.sqrt(sxx * syy)


def _trend_ratio(observed: list[float], recent: int) -> float | None:
    if len(observed) < 2 * recent:
        return None
    recent_mean = sum(observed[-recent:]) / recent
    prior_mean = sum(observed[-2 * recent : -recent]) / recent
    if prior_mean <= 0:
        return None
    return recent_mean / prior_mean


def _get_dataset_profile(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    return dataset_profile(ctx.trainval_rows, ctx.frequency)


def _get_kg_model_candidates(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    return {
        "track_key": ctx.track_key,
        "tunable_candidates": list(WHITELIST_TUNABLE_ADAPTERS),
        "registry": adapter_registry_payload(),
    }


def _get_parameter_contracts(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    adapter_id = args.get("adapter_id")
    if not isinstance(adapter_id, str) or adapter_id not in registered_adapters():
        return {"error": "ADAPTER_NOT_REGISTERED", "adapter_id": adapter_id}
    return {
        "adapter_id": adapter_id,
        "contracts": [asdict(c) for c in all_contracts_for(adapter_id, ctx.frequency)],
    }


def _get_experiment_history(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    return {"track_key": ctx.track_key, "rounds": ctx.validation_summaries}


def _get_validation_summary(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    round_index = args.get("round_index")
    for summary in ctx.validation_summaries:
        if summary.get("round_index") == round_index:
            return summary
    return {"error": "ROUND_NOT_AVAILABLE", "round_index": round_index}


def _submit_tuning_plan(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    plan = args.get("plan")
    if not isinstance(plan, dict):
        return {
            "accepted": False,
            "errors": [
                {"code": "PLAN_SCHEMA_INVALID", "message": "plan must be a JSON object", "path": "$"}
            ],
        }
    errors = validate_plan(plan, expected_snapshot_sha256=ctx.snapshot_sha256, frequency=ctx.frequency)
    return {
        "accepted": not errors,
        "errors": [asdict(e) for e in errors],
        "validator_version": PLAN_VALIDATOR_VERSION,
    }


_TOOL_HANDLERS = {
    "get_dataset_profile": _get_dataset_profile,
    "get_kg_model_candidates": _get_kg_model_candidates,
    "get_parameter_contracts": _get_parameter_contracts,
    "get_experiment_history": _get_experiment_history,
    "get_validation_summary": _get_validation_summary,
    "submit_tuning_plan": _submit_tuning_plan,
}


def dispatch_tool(name: str, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """Run one whitelisted tool; everything else is TOOL_NOT_PERMITTED."""
    handler = _TOOL_HANDLERS.get(name)
    if handler is None or name not in ALLOWED_TOOLS:
        return {"error": "TOOL_NOT_PERMITTED", "tool": name}
    return handler(args, ctx)


def build_qwen_context(ctx: ToolContext, round_index: int) -> dict[str, Any]:
    """Whitelisted planner context: exactly ``CONTEXT_KEYS``, nothing else.

    Structurally free of hidden-eval/test labels and metrics: the profile is
    computed from ``trainval_rows`` only and the history carries aggregate
    validation summaries.
    """
    return {
        "track_key": ctx.track_key,
        "frequency": ctx.frequency,
        "snapshot_sha256": ctx.snapshot_sha256,
        "round_index": round_index,
        "dataset_profile": dataset_profile(ctx.trainval_rows, ctx.frequency),
        "candidates": adapter_registry_slim(ctx.frequency),
        "budgets": {
            "max_rounds": MAX_ROUNDS,
            "max_trials_per_round": MAX_TRIALS_PER_ROUND,
            "max_total_minutes": MAX_TOTAL_MINUTES,
        },
        "targets": {
            "wape_max": TARGET_WAPE,
            "agreement_pct_min": TARGET_AGREEMENT_PCT,
            "coverage_min": COVERAGE_MIN,
            "baseline_improvement_min": BASELINE_IMPROVEMENT_MIN,
        },
        "validation_history": ctx.validation_summaries,
    }
