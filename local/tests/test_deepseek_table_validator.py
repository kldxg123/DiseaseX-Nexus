from __future__ import annotations

from deepseek_web_search_agent.query_request import build_disease_time_query_request
from deepseek_web_search_agent.source_pack_builder import mock_source_pack
from deepseek_web_search_agent.table_cleaner import clean_extracted_table_rows
from deepseek_web_search_agent.table_validator import (
    row_has_blocking_issues,
    validate_extracted_table_rows,
    validation_summary,
)


def _request():
    return build_disease_time_query_request(
        disease="Ebola",
        country_or_region="African Region",
        date_start="2026-05-01",
        date_end="2026-07-01",
    )


def test_validator_marks_good_rows_ready_for_approval() -> None:
    request = _request()
    rows = clean_extracted_table_rows(
        [
            {
                "date": "2026-05-10",
                "country": "South Africa",
                "region": "Gauteng",
                "confirmed_cases": 2,
                "source_id": "SRC1",
                "evidence_summary": "Grounded.",
                "confidence": 0.8,
            }
        ],
        request,
    )

    validated, issues = validate_extracted_table_rows(rows, request, mock_source_pack(request))

    assert validated[0]["validation_status"] == "ready_for_approval"
    assert row_has_blocking_issues(validated[0]["row_id"], issues) is False
    assert validation_summary(validated, issues)["errors_count"] == 0


def test_validator_flags_missing_source_and_out_of_range_date() -> None:
    request = _request()
    rows = clean_extracted_table_rows(
        [
            {
                "row_id": "BAD1",
                "date": "2026-08-10",
                "country": "South Africa",
                "confirmed_cases": None,
                "source_id": "UNKNOWN",
                "evidence_summary": "",
                "confidence": 0.1,
            }
        ],
        request,
    )

    validated, issues = validate_extracted_table_rows(rows, request, mock_source_pack(request))
    issue_types = {issue["issue_type"] for issue in issues}

    assert validated[0]["validation_status"] == "needs_manual_fix"
    assert "out_of_range_date" in issue_types
    assert "unknown_source" in issue_types
    assert "missing_numeric_signal" in issue_types
    assert row_has_blocking_issues("BAD1", issues) is True
