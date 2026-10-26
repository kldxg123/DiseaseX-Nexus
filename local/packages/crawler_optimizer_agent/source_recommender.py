from __future__ import annotations

from crawler_optimizer_agent.types import OptimizationAction


def rank_candidate_sources(sources: list, *, min_score: float = 0.35) -> list:
    candidates = [
        source
        for source in sources
        if getattr(source, "status", None) == "pending_review"
        and _source_score(source) >= min_score
    ]
    return sorted(candidates, key=_source_score, reverse=True)


def recommend_source_status_actions(sources: list) -> list[OptimizationAction]:
    actions: list[OptimizationAction] = []
    for source in sources:
        failures = int(getattr(source, "consecutive_failures", 0) or 0)
        source_id = str(getattr(source, "id", "")) if getattr(source, "id", None) is not None else None
        if getattr(source, "robots_allowed", None) is False:
            actions.append(
                OptimizationAction(
                    action_type="disable_source",
                    target_type="source",
                    target_id=source_id,
                    reason="Robots policy denies crawling; source should be disabled after review.",
                    confidence=0.95,
                    risk_level="high",
                    requires_human_review=True,
                    metadata={"source_status": getattr(source, "status", None)},
                )
            )
        elif failures >= 15:
            actions.append(
                OptimizationAction(
                    action_type="disable_source",
                    target_type="source",
                    target_id=source_id,
                    reason="Repeated failures exceed disable threshold; human review required.",
                    confidence=0.88,
                    risk_level="high",
                    requires_human_review=True,
                    metadata={"consecutive_failures": failures},
                )
            )
        elif failures >= 5:
            actions.append(
                OptimizationAction(
                    action_type="mark_degraded",
                    target_type="source",
                    target_id=source_id,
                    reason="Repeated failures suggest degraded source health.",
                    confidence=0.78,
                    risk_level="medium",
                    requires_human_review=False,
                    metadata={"consecutive_failures": failures},
                )
            )
    return actions


def recommend_better_sources(
    *,
    existing_sources: list,
    candidate_sources: list,
    country: str | None = None,
    disease: str | None = None,
) -> list[OptimizationAction]:
    degraded_best = max((_source_score(source) for source in existing_sources if _is_degraded(source)), default=0.0)
    actions: list[OptimizationAction] = []
    for candidate in rank_candidate_sources(candidate_sources):
        candidate_score = _source_score(candidate)
        if candidate_score <= degraded_best and degraded_best > 0:
            continue
        actions.append(
            OptimizationAction(
                action_type="add_candidate_source",
                target_type="source",
                target_id=str(getattr(candidate, "id", "")) if getattr(candidate, "id", None) is not None else None,
                reason="Pending candidate has a stronger metadata score than degraded sources.",
                confidence=min(0.9, max(0.45, candidate_score)),
                risk_level="high",
                requires_human_review=True,
                metadata={
                    "source_score": candidate_score,
                    "country": country,
                    "disease": disease,
                    "status": getattr(candidate, "status", None),
                },
            )
        )
        actions.append(
            OptimizationAction(
                action_type="manual_review",
                target_type="source",
                target_id=str(getattr(candidate, "id", "")) if getattr(candidate, "id", None) is not None else None,
                reason="Candidate source must be reviewed before activation.",
                confidence=0.8,
                risk_level="medium",
                requires_human_review=True,
                metadata={"status": getattr(candidate, "status", None)},
            )
        )
    return actions


def _is_degraded(source) -> bool:
    return getattr(source, "status", None) in {"degraded", "disabled"} or int(
        getattr(source, "consecutive_failures", 0) or 0
    ) >= 5


def _source_score(source) -> float:
    metadata = getattr(source, "metadata_json", None)
    if isinstance(metadata, dict) and metadata.get("source_score") is not None:
        try:
            return float(metadata["source_score"])
        except (TypeError, ValueError):
            pass
    fields = [
        "authority_score",
        "relevance_score",
        "freshness_score",
        "reliability_score",
        "structured_data_score",
        "historical_success_score",
    ]
    values = []
    for field in fields:
        try:
            values.append(float(getattr(source, field, 0.0) or 0.0))
        except (TypeError, ValueError):
            values.append(0.0)
    return sum(values) / len(values) if values else 0.0
