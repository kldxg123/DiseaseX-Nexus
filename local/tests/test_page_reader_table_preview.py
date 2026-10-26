from __future__ import annotations

import httpx

from deepseek_web_search_agent.page_reader import read_pages_safely
from deepseek_web_search_agent.types import GeneralWebSearchResult, RankedSearchResult


def test_page_reader_extracts_safe_table_preview() -> None:
    html = """
    <html><head><title>Influenza table</title><meta name="description" content="Weekly surveillance"></head>
    <body><h1>Influenza Surveillance</h1>
    <table>
      <tr><th>Week</th><th>State</th><th>Confirmed cases</th><th>Deaths</th></tr>
      <tr><td>Week 20</td><td>California</td><td>12</td><td>1</td></tr>
      <tr><td>Week 21</td><td>California</td><td>15</td><td>0</td></tr>
    </table></body></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"}, text=html)

    ranked = [
        RankedSearchResult(
            result=GeneralWebSearchResult(
                query="q",
                rank=1,
                title="Influenza table",
                url="https://example.org/table",
                domain="example.org",
                snippet="weekly cases",
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
    ]

    summaries, stats = read_pages_safely(
        ranked,
        allow_page_read=True,
        max_pages_to_read=1,
        max_total_http_requests=1,
        transport=httpx.MockTransport(handler),
    )

    assert stats["page_read_request_count"] == 1
    hints = summaries[0].hints
    assert hints["table_count"] == 1
    assert hints["table_headers"][:4] == ["Week", "State", "Confirmed cases", "Deaths"]
    assert hints["table_preview_rows_count"] == 2
    assert hints["date_terms_count"] >= 1
    assert hints["numeric_terms_count"] >= 1
    assert "html" not in summaries[0].to_dict()
