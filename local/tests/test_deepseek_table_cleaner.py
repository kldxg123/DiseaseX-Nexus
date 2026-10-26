from __future__ import annotations

from deepseek_web_search_agent.query_request import build_disease_time_query_request
from deepseek_web_search_agent.table_cleaner import clean_extracted_table_rows, normalize_country, normalize_date


def test_clean_extracted_rows_normalizes_fields_and_keeps_review_required() -> None:
    request = build_disease_time_query_request(
        disease="Ebola",
        country_or_region="African Region",
        date_start="2026-05-01",
        date_end="2026-07-01",
    )
    rows = clean_extracted_table_rows(
        [
            {
                "date": "2026-05-02T10:00:00Z",
                "country": " usa ",
                "region": "  Texas   ",
                "confirmed_cases": "3",
                "source_id": "SRC1",
                "evidence_summary": "Grounded.",
                "confidence": 1.2,
            }
        ],
        request,
    )

    assert rows[0]["date"] == "2026-05-02"
    assert rows[0]["country"] == "United States"
    assert rows[0]["region"] == "Texas"
    assert rows[0]["confirmed_cases"] == 3
    assert rows[0]["requires_human_review"] is True
    assert rows[0]["approval_status"] == "pending_review"
    assert rows[0]["extraction_confidence"] == 1.0


def test_cleaner_date_and_country_helpers() -> None:
    assert normalize_date("not-a-date") is None
    assert normalize_country("UK") == "United Kingdom"
