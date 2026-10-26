from __future__ import annotations

import httpx

from live_source_search_orchestrator.metadata_check import MetadataCheckRequest, run_metadata_check


def test_metadata_check_reads_one_url_once_and_extracts_safe_hints():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            text="""
            <html><head><title>Measles Surveillance Data</title>
            <meta name="description" content="United States weekly measles cases dashboard"></head>
            <body><h1>Measles cases</h1><table><tr><td>cases</td></tr></table>
            <a href="/download.csv">Download CSV data</a><a href="/api">API</a></body></html>
            """,
        )

    summary = run_metadata_check(
        [
            MetadataCheckRequest(
                url="https://www.cdc.gov/measles/data",
                title="Measles data",
                country="United States",
                disease="measles",
            )
        ],
        max_requests=3,
        allow_network=True,
        terms_reviewed=True,
        manual_reviewed=True,
        transport=httpx.MockTransport(handler),
    )

    assert calls == ["https://www.cdc.gov/measles/data"]
    assert summary.request_count == 1
    assert summary.results[0].data_source_likelihood >= 0.62
    assert summary.results[0].has_table_hint is True
    assert summary.results[0].has_download_hint is True
    assert summary.results[0].has_api_hint is True
    assert summary.full_body_saved is False
    assert summary.no_follow_links is True


def test_metadata_check_file_like_content_does_not_download_body():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/csv"}, content=b"case,count\nsecret,1\n")

    summary = run_metadata_check(
        [MetadataCheckRequest(url="https://example.gov/measles.csv", title="csv")],
        allow_network=True,
        terms_reviewed=True,
        manual_reviewed=True,
        transport=httpx.MockTransport(handler),
    )

    assert summary.request_count == 1
    assert summary.results[0].has_download_hint is True
    assert summary.results[0].files_downloaded is False
    assert summary.files_downloaded is False
