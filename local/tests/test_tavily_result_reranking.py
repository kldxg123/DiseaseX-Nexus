from __future__ import annotations

from deepseek_web_search_agent.query_planner import deterministic_query_plan
from deepseek_web_search_agent.result_ranker import rank_results
from deepseek_web_search_agent.types import DeepSeekWebSearchRequest, GeneralWebSearchResult


def test_official_structured_source_ranks_above_news_only_result() -> None:
    request = DeepSeekWebSearchRequest(disease="influenza", country="United States").validated()
    plan = deterministic_query_plan(request)
    results = [
        GeneralWebSearchResult(
            query="q",
            rank=1,
            title="Breaking news about influenza",
            url="https://news.example.com/story",
            domain="news.example.com",
            snippet="News article mentioning influenza with no table or dataset.",
        ),
        GeneralWebSearchResult(
            query="q",
            rank=2,
            title="CDC influenza weekly surveillance CSV dataset",
            url="https://www.cdc.gov/flu/weekly/data.csv",
            domain="www.cdc.gov",
            snippet="Official surveillance data table, weekly cases, CSV API.",
        ),
    ]

    ranked = rank_results(results, plan, prefer_official_and_structured_sources=True)

    assert ranked[0].result.domain == "www.cdc.gov"
    assert ranked[0].structured_time_series_likelihood >= ranked[1].structured_time_series_likelihood
