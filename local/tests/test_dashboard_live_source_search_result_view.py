from __future__ import annotations

import json

from apps.dashboard.formatters import (
    compact_live_source_search_summary,
    rows_from_live_source_search_connector_specs,
    rows_from_live_source_search_metadata_results,
    rows_from_live_source_search_provider_diagnostics,
    rows_from_live_source_search_review_queue,
)


def test_live_source_search_result_view_uses_summary_tables() -> None:
    payload = {
        "status": "ok",
        "country": "United States",
        "disease": "measles",
        "request_count_total": 3,
        "candidates_seen": 5,
        "candidates_after_dedupe": 4,
        "pending_review_inserted": 4,
        "connector_specs_generated": 2,
        "provider_failure_diagnostics": [{"provider": "data_gov", "status": "ok", "request_count": 1}],
        "review_queue": [
            {
                "title": "Measles surveillance data",
                "domain": "catalog.data.gov",
                "source_type": "csv_dataset",
                "parser_strategy": "csv_dataset",
                "recommended_action": "review_for_acquisition",
                "registry_status": "pending_review",
            }
        ],
        "metadata_check_results": [{"domain": "catalog.data.gov", "status": "confirmed"}],
        "connector_specs": [{"connector_name": "measles_csv", "auto_enable": False}],
        "response_body": "hidden",
    }

    visible = {
        "summary": compact_live_source_search_summary(payload),
        "providers": rows_from_live_source_search_provider_diagnostics(payload),
        "queue": rows_from_live_source_search_review_queue(payload),
        "metadata": rows_from_live_source_search_metadata_results(payload),
        "specs": rows_from_live_source_search_connector_specs(payload),
    }
    rendered = json.dumps(visible, ensure_ascii=False).lower()

    assert visible["summary"]["pending_review_inserted"] == 4
    assert visible["providers"][0]["provider"] == "data_gov"
    assert visible["queue"][0]["registry_status"] == "pending_review"
    assert visible["metadata"][0]["domain"] == "catalog.data.gov"
    assert visible["specs"][0]["auto_enable"] is False
    assert "response_body" not in rendered
