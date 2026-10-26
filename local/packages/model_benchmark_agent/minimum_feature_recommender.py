from __future__ import annotations

from typing import Any


DEFAULT_DISEASES = ["influenza", "norovirus"]
MANDATORY_MINIMAL = ["epidemiological_lag", "rolling_statistics"]
HIGH_VALUE_IF_AVAILABLE = ["testing", "positivity", "source_signal", "data_quality", "seasonality"]


def recommend_minimum_features(selection_ranking: dict[str, Any]) -> dict[str, Any]:
    top = list(selection_ranking.get("top_recommended_feature_groups") or [])
    not_ready = set(selection_ranking.get("not_yet_ready_feature_groups") or [])
    recommended = _ordered_unique(MANDATORY_MINIMAL + [group for group in top if group in HIGH_VALUE_IF_AVAILABLE])[:5]
    if len(recommended) < 3:
        recommended = _ordered_unique(recommended + ["seasonality"])
    payload: dict[str, Any] = {
        "schema_version": "m2_m2_minimum_feature_recommendation_v1",
        "recommendations": [],
        "global_not_yet_ready_feature_groups": sorted(not_ready),
        "limitations": [
            "Minimum set is advisory and based on local ablation only.",
            "No model retraining or production promotion is triggered.",
            "Analyst review is required before changing operational features.",
        ],
        "requires_human_review": True,
    }
    for disease in DEFAULT_DISEASES:
        payload["recommendations"].append(
            {
                "disease": disease,
                "minimal_safe_feature_set": list(MANDATORY_MINIMAL),
                "recommended_feature_set": list(recommended),
                "optional_expansion_feature_set": [
                    group for group in ["testing", "positivity", "source_signal", "data_quality"] if group not in recommended
                ],
                "missing_but_high_value_feature_set": sorted(not_ready),
                "review_note": "Use as a candidate design for governed backtests, not as an automatic production change.",
                "requires_human_review": True,
            }
        )
    return payload


def _ordered_unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value not in seen:
            ordered.append(value)
            seen.add(value)
    return ordered


__all__ = ["recommend_minimum_features"]
