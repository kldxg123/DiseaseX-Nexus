from __future__ import annotations

import json

import httpx

from apps.dashboard.api_client import DashboardApiClient
from apps.dashboard.formatters import compact_candidate_acquisition_summary


def test_dashboard_candidate_acquisition_client_uses_mock_transport():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        return httpx.Response(
            200,
            json={
                "status": "ok",
                "candidate_title": "Cases and Outbreaks",
                "candidate_domain": "www.cdc.gov",
                "network_accessed": False,
                "follow_links": False,
                "files_downloaded": False,
                "full_body_saved": False,
            },
        )

    client = DashboardApiClient("http://localhost:8000", transport=httpx.MockTransport(handler))
    payload = client.live_source_acquisition_plan({"run_id": "search_measles"})

    assert seen == ["POST /live-source-search/acquisition/plan"]
    assert payload["status"] == "ok"


def test_dashboard_candidate_acquisition_formatter_is_safe():
    payload = {
        "status": "ok",
        "candidate_title": "Cases and Outbreaks",
        "candidate_domain": "www.cdc.gov",
        "request_count": 1,
        "network_accessed": True,
        "follow_links": False,
        "files_downloaded": False,
        "full_body_saved": False,
        "raw_text": "<html>not displayed</html>",
        "metadata_json": {"raw": "not displayed"},
    }

    summary = compact_candidate_acquisition_summary(payload)
    rendered = json.dumps(summary, ensure_ascii=False).lower()

    assert summary["follow_links"] is False
    assert "raw_text" not in rendered
    assert "metadata_json" not in rendered
