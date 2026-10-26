from __future__ import annotations

from collections import Counter

from crawler_optimizer_agent.parser_diagnostics import recommend_parser_switch
from crawler_optimizer_agent.types import CrawlerFailureCase, OptimizationAction


def plan_actions_for_failure_case(
    case: CrawlerFailureCase,
    *,
    source=None,
    raw_document=None,
) -> list[OptimizationAction]:
    failure_type = case.failure_type
    target_id = case.source_id or case.raw_document_id
    if failure_type == "robots_denied":
        return [
            _action("disable_source", "source", case.source_id, "Robots policy denies access.", case, "high", True)
        ]
    if failure_type == "broken_link":
        failures = int(getattr(source, "consecutive_failures", 0) or 0)
        action_type = "disable_source" if failures >= 15 else "mark_degraded"
        risk = "high" if action_type == "disable_source" else "medium"
        return [
            _action(action_type, "source", case.source_id, "Broken or unavailable source link.", case, risk, risk == "high")
        ]
    if failure_type == "stale_source":
        return [
            _action("mark_degraded", "source", case.source_id, "Source appears stale or repeatedly failing.", case, "medium", False)
        ]
    if failure_type == "rate_limited":
        return [
            _action("reduce_crawl_frequency", "source", case.source_id, "Rate limiting detected.", case, "low", False)
        ]
    if failure_type in {"empty_content", "parser_failed", "html_structure_changed"}:
        action = recommend_parser_switch(case, raw_document=raw_document, source=source)
        return [action] if action else []
    if failure_type == "low_relevance":
        return [
            _action("lower_source_score", "source", case.source_id, "Content relevance is low for target scope.", case, "medium", False),
            _action("manual_review", "source", case.source_id, "Review whether the source belongs in this scope.", case, "medium", True),
        ]
    if failure_type == "duplicate_content":
        return [
            _action("noop", "raw_document", case.raw_document_id, "Duplicate content can be skipped safely.", case, "low", False),
            _action("manual_review", "raw_document", case.raw_document_id, "Review duplicate handling policy.", case, "low", True),
        ]
    if failure_type == "possible_better_source_found":
        return [
            _action("add_candidate_source", "source", target_id, "Better candidate source found.", case, "high", True)
        ]
    return [_action("manual_review", "source", target_id, "Unknown crawler issue requires review.", case, "medium", True)]


def deduplicate_actions(actions: list[OptimizationAction]) -> list[OptimizationAction]:
    seen: set[tuple[str, str, str | None]] = set()
    result: list[OptimizationAction] = []
    for action in actions:
        key = (action.action_type, action.target_type, action.target_id)
        if key in seen:
            continue
        seen.add(key)
        result.append(action)
    return result


def summarize_actions(actions: list[OptimizationAction]) -> dict:
    by_type = Counter(action.action_type for action in actions)
    by_risk = Counter(action.risk_level for action in actions)
    return {
        "total": len(actions),
        "by_type": dict(sorted(by_type.items())),
        "by_risk": dict(sorted(by_risk.items())),
        "requires_human_review": sum(1 for action in actions if action.requires_human_review),
    }


def _action(
    action_type: str,
    target_type: str,
    target_id: str | None,
    reason: str,
    case: CrawlerFailureCase,
    risk_level: str,
    requires_human_review: bool,
) -> OptimizationAction:
    return OptimizationAction(
        action_type=action_type,
        target_type=target_type,
        target_id=target_id,
        reason=reason,
        confidence=case.confidence,
        risk_level=risk_level,
        requires_human_review=requires_human_review,
        metadata={"failure_type": case.failure_type, "severity": case.severity},
    )
