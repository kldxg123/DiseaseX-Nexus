from __future__ import annotations

import httpx

from deepseek_web_search_agent.page_reader import read_pages_safely
from deepseek_web_search_agent.types import GeneralWebSearchResult, RankedSearchResult


def _ranked(url: str) -> RankedSearchResult:
    return RankedSearchResult(
        result=GeneralWebSearchResult(
            query="q",
            rank=1,
            title="Ebola situation report",
            url=url,
            domain=url.split("/")[2],
            snippet="Ebola cases deaths contacts by week",
        ),
        disease_relevance=1,
        country_relevance=1,
        authority=1,
        data_likelihood=1,
        structured_time_series_likelihood=1,
        recency=1,
        evidence_quality=1,
        safety_risk=0,
        total_score=1,
        source_type="official_report_evidence",
    )


def test_page_reader_builds_paragraph_signal_preview_without_raw_body() -> None:
    html = """
    <html><head><title>Ebola update</title></head><body>
    <p>On 15 May 2026, 12 confirmed cases and 3 deaths were reported in the affected area.</p>
    <p>Week 21 reported 44 contacts under monitoring.</p>
    </body></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"}, text=html)

    summaries, stats = read_pages_safely(
        [_ranked("https://example.org/ebola-report")],
        allow_page_read=True,
        max_pages_to_read=1,
        max_total_http_requests=1,
        transport=httpx.MockTransport(handler),
    )

    hints = summaries[0].hints
    assert stats["readable_pages_success"] == 1
    assert hints["paragraph_signal_preview_count"] >= 1
    assert hints["dated_numeric_signal_count"] >= 1
    assert hints["numeric_tokens_near_case_terms"] >= 1
    assert hints["raw_html_saved"] is False
