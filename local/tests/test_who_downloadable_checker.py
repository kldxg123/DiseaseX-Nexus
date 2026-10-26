from __future__ import annotations

import httpx

from deepseek_web_search_agent.downloadable_data_checker import (
    check_downloadable_metadata,
    classify_document_kind,
    is_allowed_who_domain,
)


def test_who_domain_and_document_kind_detection() -> None:
    assert is_allowed_who_domain("https://www.who.int/data/file.csv") is True
    assert is_allowed_who_domain("https://afro.who.int/publications/report.pdf") is True
    assert is_allowed_who_domain("https://example.org/file.csv") is False
    assert classify_document_kind("https://www.who.int/file.csv") == "csv_dataset"
    assert classify_document_kind("https://www.who.int/file.pdf") == "pdf_situation_report_preview"


def test_metadata_check_requires_explicit_live_controls() -> None:
    result = check_downloadable_metadata("https://www.who.int/data/file.csv")

    assert result.status == "dry_run"
    assert result.request_count == 0
    assert result.network_accessed is False
    assert result.downloadable_candidate is True


def test_metadata_check_uses_single_head_request_with_mock_transport() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return httpx.Response(200, headers={"content-type": "text/csv", "content-length": "123"})

    result = check_downloadable_metadata(
        "https://www.who.int/data/file.csv",
        allow_network=True,
        terms_reviewed=True,
        manual_reviewed=True,
        transport=httpx.MockTransport(handler),
    )

    assert calls == ["HEAD"]
    assert result.status == "ok"
    assert result.request_count == 1
    assert result.files_downloaded is False
    assert result.full_body_saved is False
