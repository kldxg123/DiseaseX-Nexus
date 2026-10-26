from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ModelEvaluationSummary:
    model_version: str
    model_name: str
    metrics: dict
    warnings: list[str]


@dataclass(frozen=True)
class PromotionDecision:
    promote: bool
    reason: str
    champion_version: str
    challenger_version: str
    checks: dict


def extract_metric(metrics: dict, path: str) -> float | None:
    current: Any = metrics
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    if current is None:
        return None
    try:
        return float(current)
    except (TypeError, ValueError):
        return None


def compare_model_metrics(
    champion: ModelEvaluationSummary,
    challenger: ModelEvaluationSummary,
    *,
    primary_metric: str = "regression.mae",
    lower_is_better: bool = True,
    min_relative_improvement: float = 0.02,
    max_false_alarm_regression: float | None = None,
    max_missed_outbreak_regression: float | None = None,
) -> PromotionDecision:
    checks: dict[str, Any] = {
        "primary_metric": primary_metric,
        "lower_is_better": lower_is_better,
        "min_relative_improvement": float(min_relative_improvement),
        "warnings": list(challenger.warnings),
    }
    blocking_warnings = [warning for warning in challenger.warnings if not _is_non_blocking_warning(warning)]
    checks["blocking_warnings"] = blocking_warnings
    if blocking_warnings:
        return _decision(False, "challenger_has_blocking_warnings", champion, challenger, checks)

    champion_value = extract_metric(champion.metrics, primary_metric)
    challenger_value = extract_metric(challenger.metrics, primary_metric)
    checks["champion_metric"] = champion_value
    checks["challenger_metric"] = challenger_value
    if champion_value is None or challenger_value is None:
        return _decision(False, "missing_primary_metric", champion, challenger, checks)

    improvement = _relative_improvement(champion_value, challenger_value, lower_is_better)
    checks["relative_improvement"] = improvement
    if improvement < min_relative_improvement:
        return _decision(False, "insufficient_relative_improvement", champion, challenger, checks)

    false_alarm_ok = _regression_check(
        champion.metrics,
        challenger.metrics,
        "classification.false_alarm_rate",
        max_false_alarm_regression,
    )
    missed_ok = _regression_check(
        champion.metrics,
        challenger.metrics,
        "classification.missed_outbreak_rate",
        max_missed_outbreak_regression,
    )
    checks["false_alarm_regression"] = false_alarm_ok
    checks["missed_outbreak_regression"] = missed_ok
    if false_alarm_ok is False:
        return _decision(False, "false_alarm_regression_exceeds_threshold", champion, challenger, checks)
    if missed_ok is False:
        return _decision(False, "missed_outbreak_regression_exceeds_threshold", champion, challenger, checks)

    return _decision(True, "challenger_meets_offline_promotion_checks", champion, challenger, checks)


def build_model_version(model_name: str, horizon_days: int, version_suffix: str = "v1") -> str:
    normalized = (model_name or "model").strip().lower().replace(" ", "_")
    suffix = (version_suffix or "v1").strip()
    return f"baseline:{normalized}:h{int(horizon_days)}:{suffix}"


def _relative_improvement(champion_value: float, challenger_value: float, lower_is_better: bool) -> float:
    denominator = max(abs(champion_value), 1e-12)
    if lower_is_better:
        return (champion_value - challenger_value) / denominator
    return (challenger_value - champion_value) / denominator


def _regression_check(
    champion_metrics: dict,
    challenger_metrics: dict,
    path: str,
    max_regression: float | None,
) -> bool | None:
    if max_regression is None:
        return None
    champion_value = extract_metric(champion_metrics, path)
    challenger_value = extract_metric(challenger_metrics, path)
    if champion_value is None or challenger_value is None:
        return False
    return (challenger_value - champion_value) <= max_regression


def _is_non_blocking_warning(warning: str) -> bool:
    normalized = str(warning).strip().lower()
    return normalized.startswith("non_blocking") or normalized.startswith("non-blocking")


def _decision(
    promote: bool,
    reason: str,
    champion: ModelEvaluationSummary,
    challenger: ModelEvaluationSummary,
    checks: dict,
) -> PromotionDecision:
    return PromotionDecision(
        promote=promote,
        reason=reason,
        champion_version=champion.model_version,
        challenger_version=challenger.model_version,
        checks=checks,
    )
