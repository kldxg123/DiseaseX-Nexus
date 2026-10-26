from __future__ import annotations

import json

import httpx

from apps.dashboard.api_client import DashboardApiClient
from apps.dashboard.formatters import compact_acquisition_quality_summary


def test_dashboard_acquisition_quality_client_uses_mock_transport():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        return httpx.Response(
            200,
            json={
                "status": "ok",
                "raw_documents_count": 1,
                "epidemic_events_count": 1,
                "model_features_count": 0,
                "event_quality_status": "insufficient_temporal_signal",
                "feature_eligible": False,
                "blocker_types": ["insufficient_temporal_signal"],
            },
        )

    client = DashboardApiClient("http://localhost:8000", transport=httpx.MockTransport(handler))
    payload = client.live_source_acquisition_quality_diagnose({"run_dir": "local", "database_url": "sqlite:///local.db"})

    assert seen == ["POST /live-source-search/acquisition/diagnose-quality"]
    assert payload["status"] == "ok"


def test_dashboard_acquisition_quality_formatter_hides_raw_payloads():
    summary = compact_acquisition_quality_summary(
        {
            "status": "ok",
            "raw_documents_count": 1,
            "epidemic_events_count": 1,
            "model_features_count": 0,
            "raw_text": "not shown",
            "metadata_json": {"not": "shown"},
            "blocker_types": ["insufficient_temporal_signal"],
        }
    )
    rendered = json.dumps(summary, ensure_ascii=False).lower()

    assert summary["model_features_count"] == 0
    assert "raw_text" not in rendered
    assert "metadata_json" not in rendered
