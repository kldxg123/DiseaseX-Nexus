from __future__ import annotations

from collections import defaultdict
from typing import Any

from model_benchmark_agent.feature_ablation import interpretability_score
from model_benchmark_agent.feature_groups import get_feature_group_registry
from model_benchmark_agent.metrics import metric_score


LOW_COST_GROUPS = {"epidemiological_lag", "rolling_statistics", "seasonality", "data_quality"}
MEDIUM_COST_GROUPS = {"testing", "positivity", "source_signal"}
NOT_YET_READY_GROUPS = {"weather", "mobility"}


def rank_feature_groups(ablation_payload: dict[str, Any]) -> dict[str, Any]:
    rows = list(ablation_payload.get("ablation_results") or [])
    registry = get_feature_group_registry()
    group_stats: dict[str, dict[str, Any]] = defaultdict(_empty_group_stats)
    for row in rows:
        if row.get("status") != "executed":
            continue
        groups = list(row.get("feature_groups") or [])
        row_score = metric_score(row.get("metrics") or {})
        stability = float(row.get("stability_score", 0.0))
        for group in groups:
            stats = group_stats[group]
            stats["runs"] += 1
            stats["score_sum"] += row_score
            stats["stability_sum"] += stability
            stats["diseases"].add(row.get("disease"))
            stats["horizons"].add(row.get("horizon_days"))
            stats["warnings"].update(row.get("warnings") or [])

    rankings: list[dict[str, Any]] = []
    for group_name, spec in registry.items():
        if group_name in NOT_YET_READY_GROUPS:
            rankings.append(_planned_group_row(group_name, spec.description))
            continue
        stats = group_stats[group_name]
        runs = int(stats["runs"])
        avg_score = stats["score_sum"] / runs if runs else 0.0
        stability_score = stats["stability_sum"] / runs if runs else 0.0
        cost_score = 1.0 - compute_feature_group_cost(group_name)
        availability_score = 1.0 if runs else 0.2
        governance_score = 0.92 if group_name in {"epidemiological_lag", "rolling_statistics", "testing", "positivity"} else 0.82
        interpretability = interpretability_score([group_name])
        total_score = (
            0.35 * avg_score
            + 0.20 * stability_score
            + 0.15 * availability_score
            + 0.12 * cost_score
            + 0.10 * interpretability
            + 0.08 * governance_score
        )
        rankings.append(
            {
                "feature_group": group_name,
                "rank": 0,
                "average_metric_score": round(avg_score, 6),
                "stability_score": round(stability_score, 6),
                "availability_score": round(availability_score, 6),
                "data_cost": round(compute_feature_group_cost(group_name), 6),
                "interpretability_score": round(interpretability, 6),
                "governance_value": round(governance_score, 6),
                "total_score": round(total_score, 6),
                "status": "available" if runs else "limited_evidence",
                "runs": runs,
                "disease_count": len(stats["diseases"]),
                "horizon_count": len(stats["horizons"]),
                "warnings": sorted(stats["warnings"])[:5],
                "requires_human_review": True,
            }
        )
    rankings.sort(key=lambda row: (row["status"] == "not_yet_ready", -row["total_score"], row["data_cost"], row["feature_group"]))
    for index, row in enumerate(rankings, start=1):
        row["rank"] = index
    available = [row for row in rankings if row["status"] != "not_yet_ready"]
    planned = [row for row in rankings if row["status"] == "not_yet_ready"]
    return {
        "schema_version": "m2_m2_feature_selection_ranking_v1",
        "feature_group_rankings": rankings,
        "top_recommended_feature_groups": [row["feature_group"] for row in available[:5]],
        "not_yet_ready_feature_groups": [row["feature_group"] for row in planned],
        "limitations": [
            "Ranking is based on local fixture/synthetic ablation only.",
            "Weather and mobility stay not-yet-ready until a governed local data source exists.",
            "Selection is advisory and requires analyst review.",
        ],
        "requires_human_review": True,
    }


def compute_feature_group_cost(group_name: str) -> float:
    if group_name in LOW_COST_GROUPS:
        return 0.2
    if group_name in MEDIUM_COST_GROUPS:
        return 0.45
    if group_name in NOT_YET_READY_GROUPS:
        return 0.9
    return 0.6


def _empty_group_stats() -> dict[str, Any]:
    return {
        "runs": 0,
        "score_sum": 0.0,
        "stability_sum": 0.0,
        "diseases": set(),
        "horizons": set(),
        "warnings": set(),
    }


def _planned_group_row(group_name: str, description: str) -> dict[str, Any]:
    return {
        "feature_group": group_name,
        "rank": 0,
        "average_metric_score": 0.0,
        "stability_score": 0.0,
        "availability_score": 0.0,
        "data_cost": compute_feature_group_cost(group_name),
        "interpretability_score": 0.7,
        "governance_value": 0.3,
        "total_score": 0.0,
        "status": "not_yet_ready",
        "runs": 0,
        "disease_count": 0,
        "horizon_count": 0,
        "warnings": [description, "No governed local feature source is available yet."],
        "requires_human_review": True,
    }


__all__ = ["compute_feature_group_cost", "rank_feature_groups"]
