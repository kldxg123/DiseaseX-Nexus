from __future__ import annotations

from deepseek_web_search_agent.deterministic_table_extractor import deterministic_rows_from_sources
from deepseek_web_search_agent.query_request import build_disease_time_query_request
from deepseek_web_search_agent.structured_table_types import SourceEvidenceItem


def test_deterministic_extractor_reads_table_and_paragraph_signals() -> None:
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
        accessed_at="2026-07-01T00:00:00Z",
        evidence_summary="safe summary",
        source_type="official_report",
        confidence=0.8,
        table_preview_rows=[
            {"date": "2026-05-15", "region": "African Region", "confirmed_cases": "12", "deaths": "3"}
        ],
        paragraph_signal_preview=[
            {"text": "On 20 May 2026, 44 contacts under monitoring were reported."}
        ],
    )

    rows = deterministic_rows_from_sources([source], request)

    assert len(rows) == 2
    assert rows[0]["date"] == "2026-05-15"
    assert rows[0]["confirmed_cases"] == 12
    assert rows[0]["confirmed_deaths"] == 3
    assert rows[1]["contacts_under_monitoring"] == 44
    assert "human_review_required" in rows[0]["uncertainty_flags"]
