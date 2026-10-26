from __future__ import annotations

import json

import httpx

from live_source_search_orchestrator.metadata_check import MetadataCheckRequest, run_metadata_check


def test_metadata_check_denied_without_explicit_flags():
    summary = run_metadata_check(
        [MetadataCheckRequest(url="https://example.gov/measles")],
        allow_network=False,
        terms_reviewed=True,
        manual_reviewed=True,
    )

    assert summary.status == "denied"
    assert summary.request_count == 0


def test_metadata_check_does_not_store_full_html_or_sensitive_strings():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<html><head><title>safe</title></head><body>password token secret key raw_text metadata_json</body></html>",
        )

    summary = run_metadata_check(
        [MetadataCheckRequest(url="https://example.gov/measles")],
        allow_network=True,
        terms_reviewed=True,
        manual_reviewed=True,
        transport=httpx.MockTransport(handler),
    )
    rendered = json.dumps(summary.to_dict(), ensure_ascii=False).lower()

    assert "<html" not in rendered
    assert "raw_text" not in rendered
    assert "metadata_json" not in rendered
    assert "password" not in rendered
    assert summary.results[0].full_body_saved is False
