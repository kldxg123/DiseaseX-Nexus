from __future__ import annotations

from deepseek_web_search_agent.query_planner import deterministic_query_plan
from deepseek_web_search_agent.types import DeepSeekWebSearchRequest


def test_structured_numeric_query_plan_prioritizes_dated_numeric_evidence() -> None:
    plan = deterministic_query_plan(
        DeepSeekWebSearchRequest(
            disease="Ebola",
            country="African Region",
            query_intent="structured_numeric_evidence",
            search_backend="tavily",
            max_search_queries=6,
        )
    )

    queries = [query.query_text.lower() for query in plan.search_queries]

    assert plan.query_intent == "structured_numeric_evidence"
    assert any("situation report" in query for query in queries)
    assert any("confirmed" in query and "deaths" in query for query in queries)
    assert "confirmed_cases" in plan.expected_metrics
    assert "confirmed_deaths" in plan.expected_metrics
    assert any("date" in item.lower() for item in plan.quality_criteria)
    assert any("numeric" in item.lower() for item in plan.quality_criteria)
