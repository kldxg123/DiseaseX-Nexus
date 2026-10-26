from __future__ import annotations

import json
from pathlib import Path

from common.db import get_session
from common.models import SourceRegistry
from source_discovery_agent.live_providers.types import LiveDiscoveryProviderResult

FIXTURE = Path("tests/fixtures/source_discovery_norovirus_search_results.json")


def _fixture_payload() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_source_discovery_api_discovers_fixture_payload_safely(api_client, api_database_url) -> None:
    response = api_client.post(
        "/source-discovery/discover-fixture",
        json={
            "country": "United States",
            "disease": "norovirus",
            "search_results": _fixture_payload(),
            "min_source_score": 0.35,
            "generate_connector_specs": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["search_results_seen"] == 8
    assert payload["candidates_found"] == 8
    assert payload["candidates_inserted_or_updated"] >= 1
    assert payload["connector_specs_generated"] >= 1
    assert payload["network_accessed"] is False
    assert len(payload["top_candidates"]) <= 10
    assert payload["top_candidates"][0]["requires_human_review"] is True

    rendered = json.dumps(payload, ensure_ascii=False).lower()
    assert "pending_review" in rendered
    assert "snippet" not in rendered
    assert "metadata_json" not in rendered
    assert "raw_text" not in rendered
    assert "feature_json" not in rendered
    assert "lesson_json" not in rendered
    assert "data table for suspected and confirmed" not in rendered

    with get_session(api_database_url) as session:
        rows = session.query(SourceRegistry).all()
        assert rows
        assert all(row.status == "pending_review" for row in rows)


def test_source_discovery_api_can_disable_connector_specs(api_client) -> None:
    response = api_client.post(
        "/source-discovery/discover-fixture",
        json={
            "country": "United States",
            "disease": "norovirus",
            "search_results": _fixture_payload(),
            "min_source_score": 0.35,
            "generate_connector_specs": False,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["connector_specs_generated"] == 0
    assert payload["connector_specs"] == []


def test_source_discovery_live_controlled_denies_without_approval(api_client) -> None:
    response = api_client.post(
        "/source-discovery/discover-live-controlled",
        json={
            "provider": "gdelt",
            "country": "United States",
            "disease": "influenza",
            "allow_network": False,
            "terms_reviewed": False,
            "manual_reviewed": False,
            "max_requests": 1,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "failed"
    assert payload["network_accessed"] is False
    assert payload["request_count"] == 0
    assert payload["candidate_urls_accessed"] is False
    assert payload["follow_links"] is False


def test_source_discovery_live_controlled_uses_provider_result_safely(
    api_client,
    api_database_url,
    monkeypatch,
) -> None:
    class FakeProvider:
        def discover(self, request):
            assert request.provider == "gdelt"
            assert request.max_requests == 1
            assert request.allow_network is True
            return LiveDiscoveryProviderResult(
                provider="gdelt",
                request_count=1,
                search_results=[
                    {
                        "title": "CDC FluView Weekly Surveillance",
                        "url": "https://www.cdc.gov/fluview/index.html",
                        "snippet": "Official influenza surveillance source candidate.",
                        "source_name": "CDC FluView",
                        "rank": 1,
                    }
                ],
                warnings=[],
                network_accessed=True,
                metadata={"status_code": 200, "request_url_domain": "api.gdeltproject.org"},
            )

    monkeypatch.setattr(
        "apps.api.routers.source_discovery.get_live_discovery_provider",
        lambda provider: FakeProvider(),
    )

    response = api_client.post(
        "/source-discovery/discover-live-controlled",
        json={
            "provider": "gdelt",
            "country": "United States",
            "disease": "influenza",
            "allow_network": True,
            "terms_reviewed": True,
            "manual_reviewed": True,
            "max_requests": 1,
            "generate_connector_specs": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["network_accessed"] is True
    assert payload["request_count"] == 1
    assert payload["candidate_urls_accessed"] is False
    assert payload["follow_links"] is False
    assert payload["candidates_found"] == 1
    assert payload["top_candidates"][0]["requires_human_review"] is True
    rendered = json.dumps(payload, ensure_ascii=False).lower()
    assert "metadata_json" not in rendered
    assert "raw_text" not in rendered
    with get_session(api_database_url) as session:
        rows = session.query(SourceRegistry).all()
        assert rows
        assert all(row.status == "pending_review" for row in rows)


def test_source_discovery_live_controlled_auto_falls_back_to_data_gov(
    api_client,
    api_database_url,
    monkeypatch,
) -> None:
    calls: list[str] = []

    class FakeProvider:
        def __init__(self, name: str) -> None:
            self.name = name

        def discover(self, request):
            calls.append(self.name)
            assert request.max_requests == 1
            assert request.allow_network is True
            if self.name == "gdelt":
                return LiveDiscoveryProviderResult(
                    provider="gdelt",
                    request_count=1,
                    search_results=[],
                    warnings=["GDELT request returned HTTP 429."],
                    network_accessed=True,
                    metadata={"status_code": 429, "request_url_domain": "api.gdeltproject.org"},
                    failure_type="rate_limited",
                    retry_allowed=False,
                    cooldown_recommended=True,
                )
            return LiveDiscoveryProviderResult(
                provider="data_gov",
                request_count=1,
                search_results=[
                    {
                        "title": "Influenza Surveillance Dataset",
                        "url": "https://catalog.data.gov/dataset/influenza-surveillance",
                        "snippet": "Weekly United States influenza surveillance data table.",
                        "source_name": "CDC",
                        "rank": 1,
                    }
                ],
                warnings=[],
                network_accessed=True,
                metadata={"status_code": 200, "request_url_domain": "catalog.data.gov"},
            )

    monkeypatch.setattr(
        "apps.api.routers.source_discovery.get_live_discovery_provider",
        lambda provider: FakeProvider(provider),
    )

    response = api_client.post(
        "/source-discovery/discover-live-controlled",
        json={
            "provider": "auto",
            "country": "United States",
            "disease": "influenza",
            "allow_network": True,
            "terms_reviewed": True,
            "manual_reviewed": True,
            "max_requests": 2,
            "generate_connector_specs": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert calls == ["gdelt", "data_gov"]
    assert payload["status"] == "ok"
    assert payload["provider"] == "auto"
    assert payload["request_count"] == 2
    assert payload["providers_attempted"] == ["gdelt", "data_gov"]
    assert payload["network_accessed"] is True
    assert payload["candidate_urls_accessed"] is False
    assert payload["follow_links"] is False
    assert payload["candidates_found"] == 1
    assert payload["top_candidates"][0]["requires_human_review"] is True
    with get_session(api_database_url) as session:
        rows = session.query(SourceRegistry).all()
        assert rows
        assert all(row.status == "pending_review" for row in rows)


def test_source_discovery_live_controlled_auto_falls_back_to_official_directory(
    api_client,
    monkeypatch,
) -> None:
    calls: list[str] = []

    class FakeProvider:
        def __init__(self, name: str) -> None:
            self.name = name

        def discover(self, request):
            calls.append(self.name)
            if self.name == "gdelt":
                return LiveDiscoveryProviderResult(
                    provider="gdelt",
                    request_count=1,
                    search_results=[],
                    warnings=["GDELT request returned HTTP 429."],
                    network_accessed=True,
                    metadata={"status_code": 429, "request_url_domain": "api.gdeltproject.org"},
                    failure_type="rate_limited",
                    cooldown_recommended=True,
                )
            if self.name == "data_gov":
                return LiveDiscoveryProviderResult(
                    provider="data_gov",
                    request_count=1,
                    search_results=[],
                    warnings=["Data.gov package_search returned HTTP 404."],
                    network_accessed=True,
                    metadata={"status_code": 404, "request_url_domain": "catalog.data.gov"},
                    failure_type="http_error",
                )
            assert request.metadata["directory_url"] == "https://www.cdc.gov/fluview/index.html"
            return LiveDiscoveryProviderResult(
                provider="official_directory",
                request_count=1,
                search_results=[
                    {
                        "title": "Weekly U.S. Influenza Surveillance Report",
                        "url": "https://www.cdc.gov/fluview/surveillance/index.html",
                        "snippet": "CDC FluView weekly United States influenza surveillance report.",
                        "source_name": "Weekly U.S. Influenza Surveillance Report",
                        "rank": 1,
                    }
                ],
                warnings=[],
                network_accessed=True,
                metadata={"status_code": 200, "request_url_domain": "www.cdc.gov"},
            )

    monkeypatch.setattr(
        "apps.api.routers.source_discovery.get_live_discovery_provider",
        lambda provider: FakeProvider(provider),
    )

    response = api_client.post(
        "/source-discovery/discover-live-controlled",
        json={
            "provider": "auto",
            "country": "United States",
            "disease": "influenza",
            "allow_network": True,
            "terms_reviewed": True,
            "manual_reviewed": True,
            "max_requests": 3,
            "generate_connector_specs": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert calls == ["gdelt", "data_gov", "official_directory"]
    assert payload["status"] == "ok"
    assert payload["request_count"] == 3
    assert payload["providers_attempted"] == ["gdelt", "data_gov", "official_directory"]
    assert payload["candidates_found"] == 1
    assert payload["top_candidates"][0]["name"] == "Weekly U.S. Influenza Surveillance Report"
