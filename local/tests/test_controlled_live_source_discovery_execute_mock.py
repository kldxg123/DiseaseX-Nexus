from __future__ import annotations

from pathlib import Path

import httpx

from scripts.run_controlled_live_source_discovery import run_controlled_live_source_discovery


def _mock_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "articles": [
                    {
                        "title": "United States influenza surveillance dataset and dashboard",
                        "url": "https://health.example.gov/influenza/dashboard",
                        "domain": "health.example.gov",
                        "sourcecountry": "US",
                        "language": "English",
                        "seendate": "20260705T090000Z",
                    },
                    {
                        "title": "United States influenza outbreak data weekly report",
                        "url": "https://publichealth.example.edu/flu/weekly",
                        "domain": "publichealth.example.edu",
                        "sourcecountry": "US",
                        "language": "English",
                        "seendate": "20260705T090100Z",
                    },
                ]
            },
        )

    return httpx.MockTransport(handler)


def test_controlled_live_source_discovery_execute_mock_creates_evidence_dir(tmp_path) -> None:
    result = run_controlled_live_source_discovery(
        provider="gdelt",
        country="United States",
        disease="influenza",
        execute_live=True,
        allow_network=True,
        terms_reviewed=True,
        manual_reviewed=True,
        max_requests=1,
        output_dir=str(tmp_path / "live_runs"),
        transport=_mock_transport(),
    )

    assert result["status"] == "ok"
    assert result["true_provider_accessed"] == "gdelt"
    assert result["true_request_count"] == 1
    assert result["network_accessed"] is True
    assert result["candidate_urls_accessed"] is False
    assert result["follow_links"] is False
    assert result["search_results_seen"] == 2
    assert result["candidates_found"] == 2
    assert result["pending_review_count"] == 2
    assert Path(result["live_output_dir"]).exists()
    assert Path(result["live_discovery_db_path"]).exists()
    assert Path(result["source_discovery_report_json"]).exists()
    assert Path(result["connector_specs_summary"]).exists()
    assert Path(result["rollback_commands"]).exists()
