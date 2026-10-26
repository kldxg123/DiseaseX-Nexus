from __future__ import annotations

import json

import httpx

from apps.dashboard.api_client import DashboardApiClient
from apps.dashboard.formatters import (
    compact_live_source_search_summary,
    rows_from_live_source_search_connector_specs,
    rows_from_live_source_search_review_queue,
)


def _payload() -> dict:
    return {
        "status": "ok",
        "run_id": "run",
        "country": "United States",
        "disease": "cholera",
        "providers_planned": ["data_gov"],
        "providers_used": ["data_gov"],
        "request_count_total": 1,
        "candidates_seen": 1,
        "candidates_after_dedupe": 1,
        "pending_review_inserted": 1,
        "connector_specs_generated": 1,
        "candidate_urls_visited": False,
        "no_follow_links": True,
        "review_queue": [
            {
                "title": "Cholera dataset",
                "domain": "catalog.data.gov",
                "source_score": 0.8,
                "source_type": "csv_dataset",
                "parser_strategy": "csv_dataset",
                "recommended_action": "controlled_acquisition_candidate",
                "registry_status": "pending_review",
                "requires_human_review": True,
            }
        ],
        "connector_specs": [
            {
                "connector_name": "cholera_dataset",
                "source_name": "Cholera dataset",
                "source_type": "csv_dataset",
                "parser_strategy": "csv_dataset",
                "recommended_action": "controlled_acquisition_candidate",
                "auto_enable": False,
                "registry_status": "pending_review",
            }
        ],
    }


def test_dashboard_client_live_source_search_uses_mock_transport():
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        return httpx.Response(200, json=_payload())

    client = DashboardApiClient("http://localhost:8000", transport=httpx.MockTransport(handler))
    payload = client.live_source_search_dry_run({"country": "United States", "disease": "cholera"})
    assert seen["path"] == "/live-source-search/dry-run"
    assert payload["status"] == "ok"


def test_dashboard_live_source_search_formatters_are_safe():
    payload = _payload()
    summary = compact_live_source_search_summary(payload)
    queue = rows_from_live_source_search_review_queue(payload)
    specs = rows_from_live_source_search_connector_specs(payload)
    rendered = json.dumps({"summary": summary, "queue": queue, "specs": specs})
    assert summary["candidate_urls_visited"] is False
    assert queue[0]["registry_status"] == "pending_review"
    assert specs[0]["auto_enable"] is False
    assert "metadata_json" not in rendered
    assert "raw_text" not in rendered
