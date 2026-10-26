from __future__ import annotations

import httpx

from deepseek_web_search_agent.page_reader import read_pages_safely
from deepseek_web_search_agent.types import GeneralWebSearchResult, RankedSearchResult


def _ranked(url: str, rank: int) -> RankedSearchResult:
    return RankedSearchResult(
        result=GeneralWebSearchResult(
            query="q",
            rank=rank,
            title=f"candidate {rank}",
            url=url,
            domain=url.split("/")[2],
            snippet="weekly table cases",
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
        source_type="html_table",
    )


def test_page_reader_skips_blocked_page_and_continues_to_structured_preview() -> None:
    html = """
    <html><head><title>Flu table</title></head><body>
    <table><tr><th>Week</th><th>Cases</th></tr><tr><td>Week 20</td><td>12</td></tr></table>
    </body></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if "blocked" in str(request.url):
            return httpx.Response(403, text="")
        return httpx.Response(200, headers={"content-type": "text/html"}, text=html)

    summaries, stats = read_pages_safely(
        [_ranked("https://example.org/blocked", 1), _ranked("https://example.org/table", 2)],
        allow_page_read=True,
        max_pages_to_read=2,
        max_total_http_requests=2,
        transport=httpx.MockTransport(handler),
    )

    assert stats["page_read_request_count"] == 2
    assert stats["blocked_pages_count"] == 1
    assert stats["readable_pages_success"] == 1
    assert summaries[-1].hints["table_preview_rows_count"] == 1
