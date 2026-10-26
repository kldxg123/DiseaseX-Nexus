from __future__ import annotations

import math
from itertools import combinations
from dataclasses import asdict, dataclass, field, replace
from typing import Any

from sqlalchemy.orm import Session

from predictor_agent.backtest import backtest_summary, rolling_origin_backtest, run_backtest
from predictor_agent.dataset import FeatureRow, load_feature_rows
from predictor_agent.metrics import summarize_classification_metrics


LOWER_IS_BETTER_METRICS = {"mae", "rmse", "mape", "smape", "brier_score"}
HIGHER_IS_BETTER_METRICS = {"interval_coverage", "f1_score", "auroc", "pr_auc"}
SUPPORTED_METRICS = LOWER_IS_BETTER_METRICS | HIGHER_IS_BETTER_METRICS
CANONICAL_FEATURE_GROUP_NAMES = (
    "case_history",
    "outbreak_history",
    "severity",
    "testing",
    "source_volume",
    "official_signals",
    "media_signals",
    "quality",
    "seasonality",
    "holiday",
)
FEATURE_GROUP_ALIASES = {
    "case_counts": "case_history",
    "outbreak_counts": "outbreak_history",
    "data_quality": "quality",
}


@dataclass(frozen=True)
class FeatureGroupSpec:
    name: str
    feature_keys: tuple[str, ...]
    cost: float = 1.0
    missingness: float = 0.0
    update_frequency: str = "weekly"
    legal_risk: float = 0.0
    description: str | None = None


@dataclass(frozen=True)
class FeatureSubsetEvaluation:
    feature_groups: tuple[str, ...]
    history_weeks: int
    score: float | None
    target_metric: str
    metric_direction: str
    passed: bool
    cost: float
    group_count: int
    records_used: int
    warnings: list[str]


@dataclass(frozen=True)
class MinimumDataAnalysisRequest:
    country: str | None
    disease: str | None
    horizon_days: int
    target_metric: str
    min_acceptable_score: float
    candidate_feature_groups: list[str] | None = None
    history_windows_weeks: list[int] = field(default_factory=lambda: [12, 26, 52, 104, 156, 260])
    model_name: str = "feature_rule"
    min_train_size: int = 8
    admin1: str | None = None
    test_window: int = 1


@dataclass(frozen=True)
class MinimumDataAnalysisResult:
    minimum_feature_set: list[str]
    minimum_history_weeks: int | None
    expected_score: float | None
    confidence_interval: tuple[float | None, float | None]
    removable_features: list[str]
    non_removable_features: list[str]
    learning_curve: list[dict]
    ablation_results: list[dict]
    cost_summary: dict
    explanation: str
    country: str | None = None
    disease: str | None = None
    horizon_days: int = 28
    target_metric: str = "mae"
    min_acceptable_score: float = 0.0
    metric_direction: str = "lower"
    evaluations: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def get_default_feature_groups() -> dict[str, FeatureGroupSpec]:
    groups = {
        "case_history": FeatureGroupSpec(
            name="case_history",
            feature_keys=(
                "current_week_cases",
                "lag_1w_cases",
                "lag_2w_cases",
                "lag_4w_cases",
                "rolling_4w_mean_cases",
                "rolling_8w_mean_cases",
            ),
            cost=0.35,
            missingness=0.15,
            update_frequency="weekly",
            legal_risk=0.05,
            description="Reported case counts and short rolling case history.",
        ),
        "outbreak_history": FeatureGroupSpec(
            name="outbreak_history",
            feature_keys=(
                "current_week_outbreaks",
                "lag_1w_outbreaks",
                "lag_2w_outbreaks",
                "lag_4w_outbreaks",
                "rolling_4w_sum_outbreaks",
                "rolling_8w_sum_outbreaks",
            ),
            cost=0.35,
            missingness=0.2,
            update_frequency="weekly",
            legal_risk=0.05,
            description="Outbreak count signals over recent reporting windows.",
        ),
        "severity": FeatureGroupSpec(
            name="severity",
            feature_keys=("current_week_deaths", "current_week_hospitalizations"),
            cost=0.45,
            missingness=0.35,
            update_frequency="weekly",
            legal_risk=0.1,
            description="Severity indicators available in some reports.",
        ),
        "testing": FeatureGroupSpec(
            name="testing",
            feature_keys=("current_week_tests",),
            cost=0.4,
            missingness=0.45,
            update_frequency="weekly",
            legal_risk=0.05,
            description="Testing volume signal.",
        ),
        "source_volume": FeatureGroupSpec(
            name="source_volume",
            feature_keys=("media_document_count", "official_document_count"),
            cost=0.5,
            missingness=0.25,
            update_frequency="weekly",
            legal_risk=0.2,
            description="Combined public media and official document-count metadata.",
        ),
        "media_signals": FeatureGroupSpec(
            name="media_signals",
            feature_keys=("media_document_count",),
            cost=0.55,
            missingness=0.25,
            update_frequency="weekly",
            legal_risk=0.25,
            description="Public media document count metadata.",
        ),
        "official_signals": FeatureGroupSpec(
            name="official_signals",
            feature_keys=("official_document_count",),
            cost=0.45,
            missingness=0.2,
            update_frequency="weekly",
            legal_risk=0.1,
            description="Official source document count metadata.",
        ),
        "quality": FeatureGroupSpec(
            name="quality",
            feature_keys=("data_quality_mean",),
            cost=0.2,
            missingness=0.1,
            update_frequency="weekly",
            legal_risk=0.0,
            description="Feature quality and source coverage summary.",
        ),
        "seasonality": FeatureGroupSpec(
            name="seasonality",
            feature_keys=("season_week_sin", "season_week_cos"),
            cost=0.15,
            missingness=0.0,
            update_frequency="weekly",
            legal_risk=0.0,
            description="Calendar seasonality indicators.",
        ),
        "holiday": FeatureGroupSpec(
            name="holiday",
            feature_keys=("holiday_flag",),
            cost=0.1,
            missingness=0.0,
            update_frequency="weekly",
            legal_risk=0.0,
            description="Local holiday placeholder indicator.",
        ),
    }
    groups["case_counts"] = replace(groups["case_history"], name="case_counts")
    groups["outbreak_counts"] = replace(groups["outbreak_history"], name="outbreak_counts")
    groups["data_quality"] = replace(groups["quality"], name="data_quality")
    return groups


def select_feature_groups(
    feature_groups: dict[str, FeatureGroupSpec],
    names: list[str] | None,
) -> list[FeatureGroupSpec]:
    if names is None:
        return [feature_groups[name] for name in CANONICAL_FEATURE_GROUP_NAMES if name in feature_groups]
    selected: list[FeatureGroupSpec] = []
    for name in names:
        normalized = str(name).strip()
        if not normalized:
            continue
        if normalized not in feature_groups:
            normalized = FEATURE_GROUP_ALIASES.get(normalized, normalized)
        try:
            selected.append(feature_groups[normalized])
        except KeyError as exc:
            raise ValueError(f"Unknown feature group: {name}.") from exc
    return selected


def filter_feature_json(feature_json: dict, allowed_keys: set[str]) -> dict:
    return {key: feature_json[key] for key in sorted(allowed_keys) if key in feature_json}


def filter_row_features(row: FeatureRow, selected_groups: list[FeatureGroupSpec]) -> FeatureRow:
    source_features = dict(row.feature_json or {})
    selected_features: dict[str, Any] = {}
    for group in selected_groups:
        for key in group.feature_keys:
            selected_features[key] = source_features.get(key, 0.0)
    return replace(row, feature_json=selected_features)


def filter_rows_by_feature_groups(rows: list[FeatureRow], selected_groups: list[FeatureGroupSpec]) -> list[FeatureRow]:
    return [filter_row_features(row, selected_groups) for row in rows]


def mask_records_to_feature_groups(
    records: list[FeatureRow],
    feature_groups: list[str],
    specs: dict[str, FeatureGroupSpec],
) -> list[FeatureRow]:
    selected_groups = select_feature_groups(specs, feature_groups)
    return filter_rows_by_feature_groups(records, selected_groups)


def keep_last_n_weeks(rows: list[FeatureRow], history_weeks: int) -> list[FeatureRow]:
    if history_weeks <= 0:
        return []
    ordered = sorted(rows, key=lambda row: (row.target_date, row.iso_week))
    selected_weeks: list[str] = []
    seen: set[str] = set()
    for row in reversed(ordered):
        if row.iso_week in seen:
            continue
        seen.add(row.iso_week)
        selected_weeks.append(row.iso_week)
        if len(selected_weeks) >= history_weeks:
            break
    selected = set(selected_weeks)
    return [row for row in ordered if row.iso_week in selected]


def truncate_records_by_history_weeks(records: list[FeatureRow], history_weeks: int) -> list[FeatureRow]:
    return keep_last_n_weeks(records, history_weeks)


def compute_feature_group_cost(feature_groups: list[str], specs: dict[str, FeatureGroupSpec]) -> float:
    return group_cost_score(select_feature_groups(specs, feature_groups))


def metric_is_higher_better(target_metric: str) -> bool:
    _path, direction = normalize_target_metric(target_metric)
    if direction == "higher":
        return True
    if direction == "lower":
        return False
    raise ValueError(f"Unsupported target_metric: {target_metric}.")


def normalize_target_metric(metric: str) -> tuple[str, str]:
    normalized = _normalize_metric(metric)
    aliases = {
        "mae": ("regression.mae", "lower"),
        "rmse": ("regression.rmse", "lower"),
        "mape": ("regression.mape", "lower"),
        "smape": ("regression.smape", "lower"),
        "brier": ("classification.brier_score", "lower"),
        "brier_score": ("classification.brier_score", "lower"),
        "f1": ("classification.f1_score", "higher"),
        "f1_score": ("classification.f1_score", "higher"),
        "auroc": ("classification.auroc", "higher"),
        "pr_auc": ("classification.average_precision", "higher"),
        "average_precision": ("classification.average_precision", "higher"),
        "interval_coverage": ("regression.interval_coverage", "higher"),
    }
    if normalized in aliases:
        return aliases[normalized]
    if "." in normalized:
        leaf = normalized.rsplit(".", 1)[-1]
        if leaf in {"mae", "rmse", "mape", "smape", "brier_score"}:
            return normalized, "lower"
        if leaf in {"f1", "f1_score", "auroc", "pr_auc", "average_precision", "interval_coverage"}:
            return normalized, "higher"
    raise ValueError(f"Unsupported target_metric: {metric}.")


def extract_metric_score(backtest_result: dict, target_metric: str) -> float | None:
    metric_path, _direction = normalize_target_metric(target_metric)
    value = extract_backtest_score(backtest_result, metric_path)
    if value is None:
        value = _find_metric_value(backtest_result, metric_path.rsplit(".", 1)[-1])
    if value is None and metric_path.endswith("average_precision"):
        value = _find_metric_value(backtest_result, "pr_auc")
    if value is None and metric_path.endswith("f1_score"):
        value = _find_metric_value(backtest_result, "f1")
    if value is None:
        return None
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(score) or math.isinf(score):
        return None
    return score


def extract_backtest_score(backtest_result: dict, target_metric: str) -> float | None:
    metric_path, _direction = normalize_target_metric(target_metric)
    current: Any = backtest_result
    for part in metric_path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
            continue
        if isinstance(current, dict) and part == "regression" and "overall" in current:
            current = current["overall"]
            continue
        if isinstance(current, dict) and part == "classification" and "classification_metrics" in current:
            current = current["classification_metrics"]
            continue
        if isinstance(current, dict) and part == "classification" and "metrics" in current:
            current = current["metrics"].get("classification", {})
            continue
        if isinstance(current, dict) and part == "regression" and "metrics" in current:
            current = current["metrics"].get("regression", {})
            continue
        return None
    if current is None:
        return None
    try:
        score = float(current)
    except (TypeError, ValueError):
        return None
    if math.isnan(score) or math.isinf(score):
        return None
    return score


def score_passes_threshold(score: float | None, min_acceptable_score: float, metric_direction: str) -> bool:
    if score is None:
        return False
    if metric_direction == "higher":
        return score >= float(min_acceptable_score)
    return score <= float(min_acceptable_score)


def run_learning_curve(
    rows: list[FeatureRow],
    *,
    selected_groups: list[FeatureGroupSpec],
    history_windows_weeks: list[int],
    target_metric: str,
    model_name: str,
    min_train_size: int,
) -> list[dict]:
    metric_is_higher_better(target_metric)
    results: list[dict] = []
    for history_weeks in history_windows_weeks:
        window_rows = keep_last_n_weeks(rows, int(history_weeks))
        filtered_rows = filter_rows_by_feature_groups(window_rows, selected_groups)
        backtest_result = _run_backtest_result(
            filtered_rows,
            target_metric=target_metric,
            model_name=model_name,
            min_train_size=min_train_size,
        )
        results.append(
            {
                "history_weeks": int(history_weeks),
                "n_rows": len(filtered_rows),
                "n_predictions": backtest_result["n_predictions"],
                "metric": _normalize_metric(target_metric),
                "score": backtest_result["score"],
                "meets_threshold": None,
            }
        )
    return results


def run_feature_group_ablation(
    rows: list[FeatureRow],
    *,
    candidate_groups: list[FeatureGroupSpec],
    target_metric: str,
    model_name: str,
    min_train_size: int,
    history_weeks: int | None = None,
) -> list[dict]:
    higher_is_better = metric_is_higher_better(target_metric)
    evaluation_rows = keep_last_n_weeks(rows, history_weeks) if history_weeks is not None else list(rows)
    baseline = _evaluate_groups(
        evaluation_rows,
        candidate_groups,
        target_metric=target_metric,
        model_name=model_name,
        min_train_size=min_train_size,
    )
    results: list[dict] = []
    for group in candidate_groups:
        ablated_groups = [candidate for candidate in candidate_groups if candidate.name != group.name]
        ablated = _evaluate_groups(
            evaluation_rows,
            ablated_groups,
            target_metric=target_metric,
            model_name=model_name,
            min_train_size=min_train_size,
        )
        delta = _metric_delta(baseline["score"], ablated["score"], higher_is_better)
        results.append(
            {
                "removed_group": group.name,
                "baseline_score": baseline["score"],
                "ablated_score": ablated["score"],
                "delta": delta,
                "importance_rank_hint": _importance_hint(delta),
            }
        )
    return results


def evaluate_feature_subset(
    records: list[FeatureRow],
    *,
    feature_groups: list[str],
    history_weeks: int,
    specs: dict[str, FeatureGroupSpec],
    target_metric: str,
    min_acceptable_score: float,
    horizon_days: int,
    model_name: str = "feature_rule",
    min_train_size: int = 8,
    test_window: int = 1,
) -> FeatureSubsetEvaluation:
    metric_path, metric_direction = normalize_target_metric(target_metric)
    warnings: list[str] = []
    try:
        selected_groups = select_feature_groups(specs, feature_groups)
    except ValueError as exc:
        return FeatureSubsetEvaluation(
            feature_groups=tuple(feature_groups),
            history_weeks=int(history_weeks),
            score=None,
            target_metric=metric_path,
            metric_direction=metric_direction,
            passed=False,
            cost=0.0,
            group_count=len(feature_groups),
            records_used=0,
            warnings=[str(exc)],
        )
    window_rows = truncate_records_by_history_weeks(records, history_weeks)
    masked_rows = filter_rows_by_feature_groups(window_rows, selected_groups)
    if len(masked_rows) <= min_train_size:
        warnings.append("insufficient records for rolling backtest")
    backtest_result = run_backtest(
        masked_rows,
        model_name=model_name,
        horizon_days=horizon_days,
        min_train_size=min_train_size,
        test_window=test_window,
    )
    warnings.extend(str(item) for item in backtest_result.get("warnings", []))
    score = extract_backtest_score(backtest_result, metric_path)
    if not backtest_result.get("predictions"):
        score = None
    elif score is not None and abs(score) < 1e-12:
        score = 0.0
    return FeatureSubsetEvaluation(
        feature_groups=tuple(group.name for group in selected_groups),
        history_weeks=int(history_weeks),
        score=score,
        target_metric=metric_path,
        metric_direction=metric_direction,
        passed=score_passes_threshold(score, min_acceptable_score, metric_direction),
        cost=compute_feature_group_cost([group.name for group in selected_groups], specs),
        group_count=len(selected_groups),
        records_used=len(masked_rows),
        warnings=sorted(set(warnings)),
    )


def generate_candidate_feature_group_sets(
    candidate_feature_groups: list[str],
    *,
    max_combinations: int = 256,
) -> list[tuple[str, ...]]:
    names = tuple(dict.fromkeys(name for name in candidate_feature_groups if str(name).strip()))
    if not names or max_combinations <= 0:
        return []
    if len(names) <= 20 and (2 ** len(names) - 1) <= max_combinations:
        return [
            combo
            for size in range(1, len(names) + 1)
            for combo in combinations(names, size)
        ]
    candidates: list[tuple[str, ...]] = []
    for size in range(1, len(names) + 1):
        for combo in combinations(names, size):
            candidates.append(combo)
            if len(candidates) >= max_combinations:
                return candidates
    return candidates


def choose_best_minimum_set(evaluations: list[FeatureSubsetEvaluation]) -> FeatureSubsetEvaluation | None:
    passing = [evaluation for evaluation in evaluations if evaluation.passed]
    if not passing:
        return None

    def sort_key(evaluation: FeatureSubsetEvaluation) -> tuple:
        if evaluation.score is None:
            score_key = float("inf")
        elif evaluation.metric_direction == "higher":
            score_key = -float(evaluation.score)
        else:
            score_key = float(evaluation.score)
        return (
            float(evaluation.cost),
            int(evaluation.group_count),
            int(evaluation.history_weeks),
            score_key,
        )

    return min(passing, key=sort_key)


def group_cost_score(groups: list[FeatureGroupSpec]) -> float:
    return round(sum(_group_cost(group) for group in groups), 6)


def greedy_select_minimum_groups(
    rows: list[FeatureRow],
    *,
    candidate_groups: list[FeatureGroupSpec],
    target_metric: str,
    min_acceptable_score: float,
    model_name: str,
    min_train_size: int,
    history_weeks: int,
) -> dict:
    higher_is_better = metric_is_higher_better(target_metric)
    evaluation_rows = keep_last_n_weeks(rows, history_weeks)
    selected: list[FeatureGroupSpec] = []
    remaining = list(candidate_groups)
    best_effort: dict | None = None

    while remaining:
        candidates: list[dict] = []
        for group in remaining:
            trial_groups = selected + [group]
            evaluation = _evaluate_groups(
                evaluation_rows,
                trial_groups,
                target_metric=target_metric,
                model_name=model_name,
                min_train_size=min_train_size,
            )
            evaluation["groups"] = trial_groups
            evaluation["added_group"] = group.name
            evaluation["cost_summary"] = _cost_summary(trial_groups)
            evaluation["meets_threshold"] = _score_meets_threshold(
                evaluation["score"],
                target_metric,
                min_acceptable_score,
            )
            candidates.append(evaluation)

        reachable = [candidate for candidate in candidates if candidate["meets_threshold"]]
        if reachable:
            chosen = min(
                reachable,
                key=lambda item: (
                    item["cost_summary"]["combined_cost_score"],
                    _score_sort_value(item["score"], higher_is_better),
                ),
            )
            return _selection_payload(chosen, target_metric, min_acceptable_score, reached_threshold=True)

        chosen = min(
            candidates,
            key=lambda item: _score_sort_value(item["score"], higher_is_better),
        )
        if best_effort is None or _is_better_score(chosen["score"], best_effort["score"], higher_is_better):
            best_effort = chosen
        selected = list(chosen["groups"])
        remaining = [group for group in remaining if group.name != chosen["added_group"]]

    if best_effort is None:
        best_effort = _evaluate_groups(
            evaluation_rows,
            [],
            target_metric=target_metric,
            model_name=model_name,
            min_train_size=min_train_size,
        )
        best_effort["groups"] = []
        best_effort["cost_summary"] = _cost_summary([])
    return _selection_payload(best_effort, target_metric, min_acceptable_score, reached_threshold=False)


class MinimumDataAnalyzer:
    def __init__(self, session: Session):
        self.session = session

    def analyze(
        self,
        request: MinimumDataAnalysisRequest | None = None,
        **kwargs,
    ) -> MinimumDataAnalysisResult:
        request = _coerce_request(request, kwargs)
        metric_path, metric_direction = normalize_target_metric(request.target_metric)
        rows = load_feature_rows(
            self.session,
            country=request.country,
            disease=request.disease,
            admin1=request.admin1,
            horizons_days=(int(request.horizon_days),),
        )
        feature_groups = get_default_feature_groups()
        candidate_groups = select_feature_groups(feature_groups, request.candidate_feature_groups)
        candidate_names = [group.name for group in candidate_groups]
        if not rows:
            return MinimumDataAnalysisResult(
                minimum_feature_set=[],
                minimum_history_weeks=None,
                expected_score=None,
                confidence_interval=(None, None),
                removable_features=[],
                non_removable_features=[],
                learning_curve=[],
                ablation_results=[],
                cost_summary=_cost_summary([]),
                explanation=(
                    "No model_features rows matched the request. No network access, retraining, or model replacement "
                    "was performed."
                ),
                country=request.country,
                disease=request.disease,
                horizon_days=request.horizon_days,
                target_metric=metric_path,
                min_acceptable_score=request.min_acceptable_score,
                metric_direction=metric_direction,
                evaluations=[],
                warnings=["No model_features rows matched the request."],
            )

        learning_curve = run_learning_curve(
            rows,
            selected_groups=candidate_groups,
            history_windows_weeks=request.history_windows_weeks,
            target_metric=request.target_metric,
            model_name=request.model_name,
            min_train_size=request.min_train_size,
        )
        for item in learning_curve:
            item["meets_threshold"] = score_passes_threshold(
                item["score"],
                request.min_acceptable_score,
                metric_direction,
            )

        evaluations: list[FeatureSubsetEvaluation] = []
        for feature_subset in generate_candidate_feature_group_sets(candidate_names):
            for history_weeks in request.history_windows_weeks:
                evaluations.append(
                    evaluate_feature_subset(
                        rows,
                        feature_groups=list(feature_subset),
                        history_weeks=int(history_weeks),
                        specs=feature_groups,
                        target_metric=metric_path,
                        min_acceptable_score=request.min_acceptable_score,
                        horizon_days=request.horizon_days,
                        model_name=request.model_name,
                        min_train_size=request.min_train_size,
                        test_window=request.test_window,
                    )
                )
        best = choose_best_minimum_set(evaluations)
        warning_messages = sorted({warning for evaluation in evaluations for warning in evaluation.warnings})

        if best is None:
            best_effort = _best_effort_evaluation(evaluations)
            selected_history = best_effort.history_weeks if best_effort is not None else _best_history(
                learning_curve,
                request.target_metric,
            )
            warning_messages.append("No feature subset met the requested threshold.")
            return MinimumDataAnalysisResult(
                minimum_feature_set=[],
                minimum_history_weeks=selected_history,
                expected_score=best_effort.score if best_effort is not None else None,
                confidence_interval=(None, None),
                removable_features=[group.name for group in candidate_groups],
                non_removable_features=[],
                learning_curve=learning_curve,
                ablation_results=[],
                cost_summary=_cost_summary([]),
                explanation=(
                    "No candidate feature subset met the requested threshold under the current rolling backtest "
                    "settings. Results do not guarantee future accuracy."
                ),
                country=request.country,
                disease=request.disease,
                horizon_days=request.horizon_days,
                target_metric=metric_path,
                min_acceptable_score=request.min_acceptable_score,
                metric_direction=metric_direction,
                evaluations=[_evaluation_to_dict(evaluation) for evaluation in evaluations],
                warnings=warning_messages,
            )

        selected_history = best.history_weeks
        selected_group_names = list(best.feature_groups)
        selected_groups = select_feature_groups(feature_groups, selected_group_names)
        ablation_results = _run_threshold_ablation(
            rows,
            selected_group_names,
            selected_history,
            specs=feature_groups,
            baseline_score=best.score,
            metric_path=metric_path,
            metric_direction=metric_direction,
            min_acceptable_score=request.min_acceptable_score,
            horizon_days=request.horizon_days,
            model_name=request.model_name,
            min_train_size=request.min_train_size,
            test_window=request.test_window,
        )
        removable, non_removable = _removable_groups(ablation_results)
        expected_score = best.score
        confidence_interval = _confidence_interval(
            [evaluation.score for evaluation in evaluations if evaluation.score is not None]
        )
        explanation = _build_explanation(
            request,
            selected_history,
            selected_group_names,
            expected_score,
            True,
        )
        return MinimumDataAnalysisResult(
            minimum_feature_set=selected_group_names,
            minimum_history_weeks=selected_history,
            expected_score=expected_score,
            confidence_interval=confidence_interval,
            removable_features=removable,
            non_removable_features=non_removable,
            learning_curve=learning_curve,
            ablation_results=ablation_results,
            cost_summary=_cost_summary(selected_groups),
            explanation=explanation,
            country=request.country,
            disease=request.disease,
            horizon_days=request.horizon_days,
            target_metric=metric_path,
            min_acceptable_score=request.min_acceptable_score,
            metric_direction=metric_direction,
            evaluations=[_evaluation_to_dict(evaluation) for evaluation in evaluations],
            warnings=warning_messages,
        )


def result_to_dict(result: MinimumDataAnalysisResult) -> dict:
    return asdict(result)


def _coerce_request(
    request: MinimumDataAnalysisRequest | None,
    kwargs: dict,
) -> MinimumDataAnalysisRequest:
    if request is not None and kwargs:
        values = asdict(request)
        values.update(kwargs)
        return MinimumDataAnalysisRequest(**values)
    if request is not None:
        return request
    return MinimumDataAnalysisRequest(**kwargs)


def _evaluation_to_dict(evaluation: FeatureSubsetEvaluation) -> dict:
    return {
        "feature_groups": list(evaluation.feature_groups),
        "history_weeks": evaluation.history_weeks,
        "score": evaluation.score,
        "target_metric": evaluation.target_metric,
        "metric_direction": evaluation.metric_direction,
        "passed": evaluation.passed,
        "cost": evaluation.cost,
        "group_count": evaluation.group_count,
        "records_used": evaluation.records_used,
        "warnings": list(evaluation.warnings),
    }


def _best_effort_evaluation(evaluations: list[FeatureSubsetEvaluation]) -> FeatureSubsetEvaluation | None:
    scored = [evaluation for evaluation in evaluations if evaluation.score is not None]
    if not scored:
        return None

    def sort_key(evaluation: FeatureSubsetEvaluation) -> tuple:
        score_key = -float(evaluation.score) if evaluation.metric_direction == "higher" else float(evaluation.score)
        return (score_key, float(evaluation.cost), int(evaluation.group_count), int(evaluation.history_weeks))

    return min(scored, key=sort_key)


def _run_backtest_result(
    rows: list[FeatureRow],
    *,
    target_metric: str,
    model_name: str,
    min_train_size: int,
) -> dict:
    if len(rows) <= min_train_size:
        return {
            "records": [],
            "summary": {},
            "overall": {},
            "classification_metrics": {},
            "score": None,
            "n_predictions": 0,
        }
    records = rolling_origin_backtest(rows, model_name=model_name, min_train_size=min_train_size)
    if not records:
        return {
            "records": [],
            "summary": {},
            "overall": {},
            "classification_metrics": {},
            "score": None,
            "n_predictions": 0,
        }
    summary = backtest_summary(records, model_name=model_name)
    classification = _classification_metrics_for_records(rows, records)
    result = {
        "records": records,
        "summary": summary,
        "overall": summary.get("overall", {}),
        "classification_metrics": classification,
        "n_predictions": len(records),
    }
    result["score"] = extract_metric_score(result, target_metric)
    return result


def _evaluate_groups(
    rows: list[FeatureRow],
    groups: list[FeatureGroupSpec],
    *,
    target_metric: str,
    model_name: str,
    min_train_size: int,
) -> dict:
    filtered_rows = filter_rows_by_feature_groups(rows, groups)
    result = _run_backtest_result(
        filtered_rows,
        target_metric=target_metric,
        model_name=model_name,
        min_train_size=min_train_size,
    )
    return {
        "score": result["score"],
        "n_rows": len(filtered_rows),
        "n_predictions": result["n_predictions"],
    }


def _run_threshold_ablation(
    rows: list[FeatureRow],
    selected_group_names: list[str],
    history_weeks: int,
    *,
    specs: dict[str, FeatureGroupSpec],
    baseline_score: float | None,
    metric_path: str,
    metric_direction: str,
    min_acceptable_score: float,
    horizon_days: int,
    model_name: str,
    min_train_size: int,
    test_window: int,
) -> list[dict]:
    results: list[dict] = []
    higher_is_better = metric_direction == "higher"
    for removed_group in selected_group_names:
        remaining = [group for group in selected_group_names if group != removed_group]
        evaluation = evaluate_feature_subset(
            rows,
            feature_groups=remaining,
            history_weeks=history_weeks,
            specs=specs,
            target_metric=metric_path,
            min_acceptable_score=min_acceptable_score,
            horizon_days=horizon_days,
            model_name=model_name,
            min_train_size=min_train_size,
            test_window=test_window,
        )
        results.append(
            {
                "removed_group": removed_group,
                "baseline_score": baseline_score,
                "ablated_score": evaluation.score,
                "delta": _metric_delta(baseline_score, evaluation.score, higher_is_better),
                "passed": evaluation.passed,
                "importance_rank_hint": "removable" if evaluation.passed else "non_removable",
                "warnings": list(evaluation.warnings),
            }
        )
    return results


def _classification_metrics_for_records(rows: list[FeatureRow], records: list) -> dict:
    label_lookup = {
        (row.country, row.admin1, row.disease, row.horizon_days, row.iso_week): bool(row.target_outbreak_label)
        for row in rows
    }
    labels: list[bool] = []
    predictions: list[float] = []
    for record in records:
        key = (record.country, record.admin1, record.disease, record.horizon_days, record.iso_week)
        if key not in label_lookup:
            continue
        labels.append(label_lookup[key])
        predictions.append(float(record.predicted_value))
    if not labels:
        return {}
    denominator = max([1.0, *[abs(value) for value in predictions], *[abs(record.actual_value) for record in records]])
    probabilities = [max(0.0, min(1.0, value / denominator)) for value in predictions]
    return summarize_classification_metrics(labels, probabilities)


def _find_metric_value(payload: Any, metric: str) -> Any:
    if not isinstance(payload, dict):
        return None
    if metric in payload:
        return payload[metric]
    for key in ("overall", "metrics", "regression_metrics", "classification_metrics"):
        nested = payload.get(key)
        if isinstance(nested, dict) and metric in nested:
            return nested[metric]
    summary = payload.get("summary")
    if isinstance(summary, dict):
        value = _find_metric_value(summary, metric)
        if value is not None:
            return value
    return None


def _normalize_metric(target_metric: str) -> str:
    return str(target_metric or "").strip().lower()


def _score_meets_threshold(score: float | None, target_metric: str, threshold: float) -> bool:
    if score is None:
        return False
    if metric_is_higher_better(target_metric):
        return score >= float(threshold)
    return score <= float(threshold)


def _score_sort_value(score: float | None, higher_is_better: bool) -> tuple[int, float]:
    if score is None:
        return (1, 0.0)
    return (0, -score if higher_is_better else score)


def _is_better_score(candidate: float | None, current: float | None, higher_is_better: bool) -> bool:
    if candidate is None:
        return False
    if current is None:
        return True
    return candidate > current if higher_is_better else candidate < current


def _metric_delta(baseline_score: float | None, ablated_score: float | None, higher_is_better: bool) -> float | None:
    if baseline_score is None or ablated_score is None:
        return None
    return baseline_score - ablated_score if higher_is_better else ablated_score - baseline_score


def _importance_hint(delta: float | None) -> str:
    if delta is None:
        return "unknown"
    if delta > 0:
        return "non_removable"
    return "removable"


def _removable_groups(ablation_results: list[dict]) -> tuple[list[str], list[str]]:
    removable: list[str] = []
    non_removable: list[str] = []
    for item in ablation_results:
        group_name = str(item["removed_group"])
        if item.get("importance_rank_hint") == "non_removable":
            non_removable.append(group_name)
        else:
            removable.append(group_name)
    return removable, non_removable


def _selection_payload(evaluation: dict, target_metric: str, threshold: float, *, reached_threshold: bool) -> dict:
    groups = list(evaluation.get("groups", []))
    score = evaluation.get("score")
    reached = reached_threshold and _score_meets_threshold(score, target_metric, threshold)
    return {
        "minimum_feature_set": [group.name for group in groups],
        "expected_score": score,
        "n_rows": evaluation.get("n_rows", 0),
        "n_predictions": evaluation.get("n_predictions", 0),
        "reached_threshold": reached,
        "cost_summary": evaluation.get("cost_summary", _cost_summary(groups)),
    }


def _first_meeting_history(learning_curve: list[dict]) -> int | None:
    for item in learning_curve:
        if item.get("meets_threshold"):
            return int(item["history_weeks"])
    return None


def _best_history(learning_curve: list[dict], target_metric: str) -> int | None:
    higher_is_better = metric_is_higher_better(target_metric)
    scored = [item for item in learning_curve if item.get("score") is not None]
    if not scored:
        return None
    best = min(scored, key=lambda item: _score_sort_value(item["score"], higher_is_better))
    return int(best["history_weeks"])


def _confidence_interval(scores: list[float]) -> tuple[float | None, float | None]:
    values = [float(score) for score in scores if score is not None]
    if len(values) < 2:
        return (None, None)
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    margin = 1.96 * math.sqrt(variance) / math.sqrt(len(values))
    return (max(0.0, mean - margin), max(0.0, mean + margin))


def _cost_summary(groups: list[FeatureGroupSpec]) -> dict:
    return {
        "group_count": len(groups),
        "total_cost": round(sum(_clamp01(group.cost) for group in groups), 6),
        "total_missingness": round(sum(_clamp01(group.missingness) for group in groups), 6),
        "total_legal_risk": round(sum(_clamp01(group.legal_risk) for group in groups), 6),
        "combined_cost_score": group_cost_score(groups),
        "groups": [group.name for group in groups],
    }


def _group_cost(group: FeatureGroupSpec) -> float:
    return _clamp01(group.cost) + (0.5 * _clamp01(group.missingness)) + (0.5 * _clamp01(group.legal_risk))


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _build_explanation(
    request: MinimumDataAnalysisRequest,
    history_weeks: int,
    feature_set: list[str],
    expected_score: float | None,
    reached_threshold: bool,
) -> str:
    status = "reached" if reached_threshold else "did not reach"
    score_text = "null" if expected_score is None else f"{expected_score:.4f}"
    return (
        f"The minimum data analysis {status} the requested threshold for {request.target_metric} "
        f"({request.min_acceptable_score}) under the current historical model_features and rolling backtest settings. "
        f"The selected history window is {history_weeks} weeks and the selected feature groups are {feature_set}. "
        f"The observed backtest score is {score_text}. This is a local decision-support analysis only and does not "
        "guarantee future accuracy. No network access, automatic retraining, or production model replacement was "
        "performed."
    )


__all__ = [
    "FeatureGroupSpec",
    "FeatureSubsetEvaluation",
    "MinimumDataAnalysisRequest",
    "MinimumDataAnalysisResult",
    "MinimumDataAnalyzer",
    "choose_best_minimum_set",
    "compute_feature_group_cost",
    "evaluate_feature_subset",
    "extract_backtest_score",
    "extract_metric_score",
    "filter_feature_json",
    "filter_row_features",
    "filter_rows_by_feature_groups",
    "generate_candidate_feature_group_sets",
    "get_default_feature_groups",
    "greedy_select_minimum_groups",
    "group_cost_score",
    "keep_last_n_weeks",
    "mask_records_to_feature_groups",
    "metric_is_higher_better",
    "normalize_target_metric",
    "result_to_dict",
    "run_feature_group_ablation",
    "run_learning_curve",
    "score_passes_threshold",
    "select_feature_groups",
    "truncate_records_by_history_weeks",
]
