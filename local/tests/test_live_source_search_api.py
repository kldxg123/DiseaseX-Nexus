from __future__ import annotations

import json

from fastapi.testclient import TestClient

from apps.api.deps import get_db_session
from apps.api.main import app
from source_discovery_agent.live_providers.types import LiveDiscoveryProviderResult


def test_live_source_search_api_dry_run(api_client):
    response = api_client.post(
        "/live-source-search/dry-run",
        json={"country": "United States", "disease": "cholera", "provider": "auto"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["request_count_total"] == 0
    assert payload["candidate_urls_visited"] is False


def test_live_source_search_api_dry_run_does_not_require_database_dependency(tmp_path):
    app.dependency_overrides.pop(get_db_session, None)
    with TestClient(app) as client:
        response = client.post(
            "/live-source-search/dry-run",
            json={
                "country": "United States",
                "disease": "influenza",
                "provider": "auto",
                "output_dir": str(tmp_path / "reports"),
            },
        )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["request_count_total"] == 0
    assert payload["candidate_urls_visited"] is False


def test_live_source_search_api_run_uses_mocked_provider(api_client, monkeypatch):
    class FakeProvider:
        def __init__(self, name: str) -> None:
            self.name = name

        def discover(self, request):
            return LiveDiscoveryProviderResult(
                provider=self.name,
                request_count=1,
                search_results=[
                    {
                        "title": "Cholera surveillance dataset",
                        "url": "https://catalog.data.gov/dataset/cholera-surveillance",
                        "snippet": "United States cholera surveillance dataset CSV.",
                        "source_name": "Data.gov",
                        "rank": 1,
                    }
                ]
                if self.name == "data_gov"
                else [],
                warnings=[],
                network_accessed=True,
                metadata={"status_code": 200, "request_url_domain": "catalog.data.gov"},
            )

    monkeypatch.setattr(
        "live_source_search_orchestrator.provider_runner.get_live_discovery_provider",
        lambda provider: FakeProvider(provider),
    )
    response = api_client.post(
        "/live-source-search/run",
        json={
            "country": "United States",
            "disease": "cholera",
            "provider": "data_gov",
            "allow_network": True,
            "terms_reviewed": True,
            "manual_reviewed": True,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["pending_review_inserted"] == 1
    assert payload["review_queue"]
    rendered = json.dumps(payload, ensure_ascii=False).lower()
    assert "metadata_json" not in rendered
    assert "raw_text" not in rendered
