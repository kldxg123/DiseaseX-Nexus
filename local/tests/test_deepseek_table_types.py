from __future__ import annotations

from deepseek_web_search_agent.query_request import build_disease_time_query_request
from deepseek_web_search_agent.structured_table_types import DEFAULT_REQUESTED_FIELDS, SourceEvidenceItem


def test_disease_time_query_request_is_safe_and_bounded() -> None:
    request = build_disease_time_query_request(
        disease=" Ebola ",
        country_or_region=" African Region ",
        date_start="2026-05-01",
        date_end="2026-07-01",
        max_sources=99,
    )

    assert request.disease == "Ebola"
    assert request.country_or_region == "African Region"
    assert request.max_sources == 10
    assert request.require_human_approval is True
    assert request.allow_deepseek is False
    assert request.requested_fields == DEFAULT_REQUESTED_FIELDS
    assert request.to_dict()["date_start"] == "2026-05-01"


def test_source_evidence_item_never_marks_full_text_saved_by_default() -> None:
    source = SourceEvidenceItem(
        source_id="SRC1",
        title_summary="Official update",
        url_safe_summary="https://example.org/report",
        domain="example.org",
        evidence_summary="Short safe summary.",
    )

    payload = source.to_dict()
    assert payload["full_text_saved"] is False
    assert "raw_text" not in payload
    assert "html" not in payload
