from __future__ import annotations

import httpx

from live_source_search_orchestrator.metadata_check import run_metadata_check


def test_metadata_check_extracts_dated_numeric_signal_profile() -> None:
    html = """
    <html><head><title>Ebola situation report</title></head><body>
    <h1>Ebola situation report African Region</h1>
    <p>On 15 May 2026, 12 confirmed cases and 3 deaths were reported.</p>
    <p>Week 21 reported 44 contacts under monitoring.</p>
    </body></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"}, text=html)

    summary = run_metadata_check(
        [
            {
                "url": "https://example.org/ebola-situation-report",
                "title": "Ebola situation report",
                "country": "African Region",
                "disease": "Ebola",
            }
        ],
        max_requests=1,
        allow_network=True,
        terms_reviewed=True,
        manual_reviewed=True,
        transport=httpx.MockTransport(handler),
    )

    result = summary.results[0]
    assert summary.request_count == 1
    assert result.situation_report_hint is True
    assert result.dated_numeric_signal_count >= 1
    assert result.numeric_tokens_near_case_terms >= 1
    assert result.numeric_tokens_near_death_terms >= 1
    assert result.extraction_ready_hint is True
    assert result.full_body_saved is False
    assert result.files_downloaded is False
