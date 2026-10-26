from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from model_benchmark_agent.metrics import metric_score
from model_benchmark_agent.model_registry import get_model_spec
from model_benchmark_agent.types import ModelFeatureRanking, BenchmarkResult, result_from_dict


def rank_benchmark_results(results_payload: dict[str, Any] | list[dict[str, Any]]) -> dict[str, Any]:
    raw_results = results_payload.get("results", []) if isinstance(results_payload, dict) else results_payload
    results = [result_from_dict(item) for item in raw_results]
    rankings: list[ModelFeatureRanking] = []
    for result in results:
        spec = get_model_spec(result.model_name)
        score_components = score_result(result)
        limitations = list(result.warnings)
        if result.status != "executed" and not result.status.endswith("_proxy"):
            limitations.append(f"Not eligible for top recommendation because status={result.status}.")
        reason = build_recommendation_reason(result, score_components)
        rankings.append(
            ModelFeatureRanking(
                rank=0,
                model_name=result.model_name,
                model_family=result.model_family,
                feature_groups=list(result.feature_groups),
                metric_score=score_components["metric_score"],
                evidence_score=score_components["evidence_score"],
                data_fit_score=score_components["data_fit_score"],
                interpretability_score=score_components["interpretability_score"],
                implementation_feasibility_score=score_components["implementation_feasibility_score"],
                safety_score=score_components["safety_score"],
                total_score=score_components["total_score"],
                recommendation_reason=reason,
                limitations=sorted(set(limitations)),
                status=result.status,
                requires_human_review=True,
            )
        )
    rankings.sort(key=lambda item: (item.total_score, item.metric_score, item.safety_score), reverse=True)
    eligible_first = [item for item in rankings if _eligible_for_top(item)]
    ineligible = [item for item in rankings if not _eligible_for_top(item)]
    ordered = eligible_first + ineligible
    ranked = [
        ModelFeatureRanking(**{**item.to_dict(), "rank": index + 1})
        for index, item in enumerate(ordered)
    ]
    return {
        "schema_version": "m2_m1_model_feature_ranking_v1",
        "rankings": [item.to_dict() for item in ranked],
        "top_5": [item.to_dict() for item in ranked[:5]],
        "feature_groups_top_5": top_feature_group_sets(results),
        "limitations": [
            "Skipped and insufficient_data models cannot rank first.",
            "Deep learning, transformer, graph, and LLM agent candidates are deferred for safety and data sufficiency.",
            "Ranking is a benchmark recommendation, not a production promotion decision.",
        ],
    }


def load_results(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def score_result(result: BenchmarkResult) -> dict[str, float]:
    if result.status in {"skipped", "insufficient_data", "failed"}:
        return {
            "metric_score": 0.0,
            "evidence_score": 0.0,
            "data_fit_score": 0.0,
            "interpretability_score": 0.0,
            "implementation_feasibility_score": 0.0,
            "safety_score": 0.0,
            "total_score": 0.0,
        }
    spec = get_model_spec(result.model_name)
    metric = metric_score(result.metrics)
    evidence = 0.8 if spec.recommended_for_m2_m1 else 0.45
    data_fit = min(1.0, 0.35 + 0.1 * len(result.feature_groups) + min(0.25, result.test_rows / 40.0))
    interpretability = _interpretability_score(spec.interpretability)
    feasibility = _risk_score(spec.compute_risk)
    safety = min(_risk_score(spec.leakage_risk), 0.95 if "proxy" not in result.status else 0.78)
    total = (
        0.32 * metric
        + 0.16 * evidence
        + 0.14 * data_fit
        + 0.16 * interpretability
        + 0.12 * feasibility
        + 0.10 * safety
    )
    return {
        "metric_score": round(metric, 6),
        "evidence_score": round(evidence, 6),
        "data_fit_score": round(data_fit, 6),
        "interpretability_score": round(interpretability, 6),
        "implementation_feasibility_score": round(feasibility, 6),
        "safety_score": round(safety, 6),
        "total_score": round(total, 6),
    }


def build_recommendation_reason(result: BenchmarkResult, scores: dict[str, float]) -> str:
    if scores["total_score"] <= 0:
        return f"{result.model_name} is not recommended in this run because status is {result.status}."
    return (
        f"{result.model_name} with {', '.join(result.feature_groups)} balances local backtest "
        f"metrics, evidence fit, interpretability, feasibility, and safety for M2 M1 validation."
    )


def top_feature_group_sets(results: list[BenchmarkResult], limit: int = 5) -> list[list[str]]:
    scores: dict[tuple[str, ...], list[float]] = defaultdict(list)
    counts: Counter[tuple[str, ...]] = Counter()
    for result in results:
        key = tuple(result.feature_groups)
        counts[key] += 1
        if result.status not in {"skipped", "insufficient_data", "failed"}:
            scores[key].append(metric_score(result.metrics))
    ranked = sorted(
        counts,
        key=lambda key: (sum(scores[key]) / len(scores[key]) if scores[key] else 0.0, counts[key]),
        reverse=True,
    )
    return [list(key) for key in ranked[:limit]]


def ranking_to_markdown(payload: dict[str, Any]) -> str:
    rankings = payload.get("rankings", [])
    lines = [
        "# M2 M1 Model and Feature Ranking",
        "",
        "This ranking is based on local benchmark results and conservative safety rules.",
        "It does not automatically promote any model.",
        "",
        "| rank | model | status | total_score | feature_groups | reason |",
        "|---:|---|---|---:|---|---|",
    ]
    for item in rankings[:30]:
        lines.append(
            f"| {item['rank']} | {item['model_name']} | {item['status']} | "
            f"{item['total_score']:.3f} | {', '.join(item['feature_groups'])} | "
            f"{item['recommendation_reason']} |"
        )
    lines.extend(
        [
            "",
            "## Why Deep Learning / Transformer / LLM Agent Are Not Recommended Yet",
            "",
            "- Current M1 data is small and still expanding across diseases and sources.",
            "- Higher-capacity models carry greater leakage, compute, explainability, and governance risk.",
            "- M2 M1 prioritizes verifiable, interpretable baselines and proxy checks before any larger model work.",
        ]
    )
    return "\n".join(lines) + "\n"


def _eligible_for_top(item: ModelFeatureRanking) -> bool:
    return item.total_score > 0 and item.status not in {"skipped", "insufficient_data", "failed"}


def _interpretability_score(value: str) -> float:
    return {"high": 1.0, "medium": 0.65, "low": 0.25}.get(value, 0.5)


def _risk_score(value: str) -> float:
    return {"low": 1.0, "medium": 0.65, "high": 0.2}.get(value, 0.5)


__all__ = [
    "build_recommendation_reason",
    "load_results",
    "rank_benchmark_results",
    "ranking_to_markdown",
    "score_result",
    "top_feature_group_sets",
]
