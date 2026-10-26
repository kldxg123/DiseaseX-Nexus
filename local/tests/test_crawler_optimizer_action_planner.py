from __future__ import annotations

from datetime import UTC, datetime

from crawler_optimizer_agent.action_planner import (
    deduplicate_actions,
    plan_actions_for_failure_case,
    summarize_actions,
)
from crawler_optimizer_agent.types import CrawlerFailureCase


def _case(failure_type: str) -> CrawlerFailureCase:
    return CrawlerFailureCase(
        source_id="1",
        raw_document_id="2",
        source_name="Fixture",
        url="https://fixture.invalid/a",
        failure_type=failure_type,
        severity="high",
        confidence=0.8,
        evidence={},
        created_at=datetime.now(UTC),
    )


def test_action_planner_maps_common_failures() -> None:
    assert plan_actions_for_failure_case(_case("robots_denied"))[0].action_type == "disable_source"
    assert plan_actions_for_failure_case(_case("rate_limited"))[0].action_type == "reduce_crawl_frequency"
    assert plan_actions_for_failure_case(_case("parser_failed"))[0].action_type in {
        "switch_parser",
        "retry_with_different_parser",
    }
    low_relevance_actions = plan_actions_for_failure_case(_case("low_relevance"))
    assert {action.action_type for action in low_relevance_actions} == {"lower_source_score", "manual_review"}


def test_high_risk_actions_require_review() -> None:
    action = plan_actions_for_failure_case(_case("robots_denied"))[0]

    assert action.risk_level == "high"
    assert action.requires_human_review is True


def test_action_summary_and_deduplication() -> None:
    actions = plan_actions_for_failure_case(_case("duplicate_content"))
    deduped = deduplicate_actions(actions + actions)
    summary = summarize_actions(deduped)

    assert len(deduped) == 2
    assert summary["total"] == 2
    assert summary["requires_human_review"] == 1
