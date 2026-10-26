from __future__ import annotations

import httpx

from deepseek_web_search_agent.document_preview_reader import preview_document_candidate


def test_document_preview_reads_small_csv_without_saving_full_file() -> None:
    csv_text = "date,cases,deaths\n2026-05-15,12,3\n"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/csv", "content-length": str(len(csv_text))}, text=csv_text)

    result = preview_document_candidate(
        "https://example.org/ebola.csv",
        allow_document_preview=True,
        terms_reviewed=True,
        manual_reviewed=True,
        transport=httpx.MockTransport(handler),
    )

    assert result.status == "ok"
    assert result.document_type == "csv"
    assert result.table_like_text_blocks
    assert result.full_body_saved is False
    assert result.files_downloaded is False


def test_document_preview_skips_pdf_without_text_dependency() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "application/pdf", "content-length": "1200"}, content=b"%PDF")

    result = preview_document_candidate(
        "https://example.org/report.pdf",
        allow_document_preview=True,
        terms_reviewed=True,
        manual_reviewed=True,
        transport=httpx.MockTransport(handler),
    )

    assert result.status == "skipped_pdf"
    assert "pdf_text_extraction_unavailable" in result.warnings
    assert result.full_body_saved is False
