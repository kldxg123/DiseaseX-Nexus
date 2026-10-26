from __future__ import annotations

from deepseek_web_search_agent.query_planner import deterministic_query_plan
from deepseek_web_search_agent.search_runner import run_search_plan
from deepseek_web_search_agent.types import DeepSeekWebSearchRequest
from deepseek_web_search_agent.web_search_providers import MockSearchProvider


def test_expanded_retrieval_budget_clamps_to_safe_limits() -> None:
    request = DeepSeekWebSearchRequest(
        disease="Ebola",
        country="African Region",
        max_search_queries=99,
        max_search_results_per_query=99,
        max_pages_to_read=99,
        max_total_http_requests=99,
    ).validated()

    assert request.max_search_queries == 6
    assert request.max_search_results_per_query == 8
    assert request.max_pages_to_read == 8
    assert request.max_total_http_requests == 16


def test_search_runner_respects_request_budget() -> None:
    request = DeepSeekWebSearchRequest(
        disease="Ebola",
        country="African Region",
        max_search_queries=6,
    ).validated()
    plan = deterministic_query_plan(request)

    results, stats = run_search_plan(
        plan,
        provider=MockSearchProvider(),
        allow_network=False,
        max_results_per_query=8,
        max_total_http_requests=3,
    )

    assert stats["search_request_count"] == 3
    assert len(results) <= 9
    assert stats["candidate_urls_accessed"] is False
    assert stats["follow_links"] is False
