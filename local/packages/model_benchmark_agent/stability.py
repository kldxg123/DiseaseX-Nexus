from __future__ import annotations

import statistics
from collections import defaultdict
from typing import Any

from model_benchmark_agent.metrics import metric_score


def analyze_stability(ablation_payload: dict[str, Any]) -> dict[str, Any]:
    rows = [row for row in ablation_payload.get("ablation_results", []) if row.get("status") == "executed"]
    by_key: dict[tuple[str, str], list[float]] = defaultdict(list)
    by_horizon: dict[int, list[float]] = defaultdict(list)
    by_disease: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        score = metric_score(row.get("metrics") or {})
        by_key[(row.get("model_name", ""), row.get("feature_set_name", ""))].append(score)
        by_horizon[int(row.get("horizon_days", 0))].append(score)
        by_disease[str(row.get("disease", ""))].append(score)
    stable: list[dict[str, Any]] = []
    unstable: list[dict[str, Any]] = []
    for (model_name, feature_set_name), scores in sorted(by_key.items()):
        variance = _variance(scores)
        row = {
            "model_name": model_name,
            "feature_set_name": feature_set_name,
            "run_count": len(scores),
            "average_score": round(sum(scores) / len(scores), 6) if scores else 0.0,
            "score_variance": round(variance, 6),
        }
        if len(scores) >= 4 and variance <= 0.015:
            stable.append(row)
        else:
            unstable.append(row)
    return {
        "schema_version": "m2_m2_stability_analysis_v1",
        "stable_recommendations": sorted(stable, key=lambda row: row["average_score"], reverse=True)[:10],
        "unstable_recommendations": sorted(unstable, key=lambda row: row["score_variance"], reverse=True)[:10],
        "needs_more_data": len(rows) < 20,
        "rank_stability_across_horizons": _summary_by_int_key(by_horizon),
        "disease_consistency": _summary_by_str_key(by_disease),
        "fold_variance": round(_variance([metric_score(row.get("metrics") or {}) for row in rows]), 6),
        "small_sample_warning": "Ablation uses local fixture/synthetic samples only; expand governed data before promotion.",
        "feature_contribution_uncertainty": "Contribution direction is indicative, not causal.",
        "requires_human_review": True,
    }


def _summary_by_int_key(groups: dict[int, list[float]]) -> list[dict[str, Any]]:
    return [
        {
            "horizon_days": key,
            "run_count": len(values),
            "average_score": round(sum(values) / len(values), 6) if values else 0.0,
            "variance": round(_variance(values), 6),
        }
        for key, values in sorted(groups.items())
    ]


def _summary_by_str_key(groups: dict[str, list[float]]) -> list[dict[str, Any]]:
    return [
        {
            "disease": key,
            "run_count": len(values),
            "average_score": round(sum(values) / len(values), 6) if values else 0.0,
            "variance": round(_variance(values), 6),
        }
        for key, values in sorted(groups.items())
    ]


def _variance(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    return statistics.variance(values)


__all__ = ["analyze_stability"]
