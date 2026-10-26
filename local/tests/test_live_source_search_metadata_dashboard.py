from __future__ import annotations

from apps.dashboard.formatters import (
    compact_live_source_search_summary,
    rows_from_live_source_search_metadata_results,
    rows_from_live_source_search_review_queue,
)


def test_dashboard_formats_metadata_and_quality_fields_safely():
    payload = {
        "quality_gate_rejected": 2,
        "metadata_check_executed": True,
        "metadata_check_request_count": 1,
        "metadata_check_results": [
            {
                "domain": "www.cdc.gov",
                "status": "data_source_candidate",
                "data_source_likelihood": 0.8,
                "detected_source_type": "html_table",
                "parser_strategy_hint": "html_table",
                "recommended_action": "manual_review_connector_candidate",
            }
        ],
        "review_queue": [
            {
                "title": "Measles data",
                "domain": "www.cdc.gov",
                "source_score": 0.5,
                "refined_score": 0.7,
                "quality_gate_status": "needs_metadata_check",
                "metadata_check_status": "data_source_candidate",
            }
        ],
    }

    summary = compact_live_source_search_summary(payload)
    assert summary["quality_gate_rejected"] == 2
    assert rows_from_live_source_search_metadata_results(payload)[0]["domain"] == "www.cdc.gov"
    assert rows_from_live_source_search_review_queue(payload)[0]["quality_gate_status"] == "needs_metadata_check"
