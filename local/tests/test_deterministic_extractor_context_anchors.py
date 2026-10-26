from __future__ import annotations

from deepseek_web_search_agent.deterministic_table_extractor import deterministic_rows_from_sources
from deepseek_web_search_agent.query_request import build_disease_time_query_request
from deepseek_web_search_agent.structured_table_types import SourceEvidenceItem


def test_deterministic_extractor_uses_report_date_fallback_with_review_flags() -> None:
    request = build_disease_time_query_request(
        disease="Ebola",
        country_or_region="African Region",
        date_start="2026-05-01",
        date_end="2026-07-01",
    )
    source = SourceEvidenceItem(
        source_id="SRC1",
        title_summary="Ebola situation report",
        url_safe_summary="https://example.org/report",
        domain="example.org",
        published_date_hint="2026-05-15",
        paragraph_signal_preview=[{"text": "12 confirmed cases and 3 deaths were reported."}],
    )

    rows = deterministic_rows_from_sources([source], request)

    assert rows
    assert rows[0]["date_source"] == "report_date_fallback"
    assert rows[0]["requires_human_review"] if "requires_human_review" in rows[0] else True
    assert "report_date_fallback" in rows[0]["uncertainty_flags"]
    assert rows[0]["confidence"] <= 0.68
