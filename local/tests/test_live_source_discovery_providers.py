from __future__ import annotations

import socket

from common.db import get_session, init_db
from common.models import SourceRegistry
from source_discovery_agent import SourceDiscoveryAgent
from source_discovery_agent.live_providers import (
    LiveDiscoveryRequest,
    get_live_discovery_provider,
    list_live_discovery_providers,
)
from source_discovery_agent.live_providers.data_gov_provider import (
    build_data_gov_url,
    data_gov_payload_to_search_results,
)
from source_discovery_agent.live_providers.gdelt_provider import build_gdelt_query, build_gdelt_url


def test_live_discovery_provider_registry_and_dry_run_behavior(monkeypatch) -> None:
    def fail_network(*args, **kwargs):
        raise AssertionError("network must not be used")

    monkeypatch.setattr(socket, "create_connection", fail_network)
    assert {"mock", "gdelt", "data_gov", "official_directory"}.issubset(set(list_live_discovery_providers()))

    gdelt_result = get_live_discovery_provider("gdelt").discover(
        LiveDiscoveryRequest(
            country="United States",
            disease="influenza",
            provider="gdelt",
            allow_network=True,
        )
    )
    official_result = get_live_discovery_provider("official_directory").discover(
        LiveDiscoveryRequest(
            country="United States",
            disease="influenza",
            provider="official_directory",
        )
    )

    assert gdelt_result.network_accessed is False
    assert gdelt_result.request_count == 0
    assert any("not enabled" in warning for warning in gdelt_result.warnings)
    assert official_result.network_accessed is False
    assert len(official_result.search_results) >= 1


def test_gdelt_provider_builds_query_and_url_without_request() -> None:
    query = build_gdelt_query("United States", "influenza")
    url = build_gdelt_url(query=query, max_records=1)

    assert "influenza" in query.lower()
    assert "api.gdeltproject.org" in url
    assert "format=json" in url


def test_data_gov_provider_parses_package_search_without_candidate_visits() -> None:
    payload = {
        "success": True,
        "result": {
            "results": [
                {
                    "name": "influenza-surveillance",
                    "title": "Influenza Surveillance Dataset",
                    "notes": "Weekly influenza surveillance data table.",
                    "metadata_modified": "2026-01-02T00:00:00",
                    "organization": {"title": "CDC"},
                    "tags": [{"name": "influenza"}, {"name": "surveillance"}],
                    "resources": [{"format": "CSV"}],
                }
            ]
        },
    }

    rows = data_gov_payload_to_search_results(payload)
    url = build_data_gov_url(query="United States influenza surveillance", max_records=10)

    assert "catalog.data.gov" in url
    assert rows[0]["source_name"] == "CDC"
    assert rows[0]["url"] == "https://catalog.data.gov/dataset/influenza-surveillance"
    assert "CSV" in rows[0]["snippet"]


def test_source_discovery_agent_can_use_mock_provider_result(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'provider-result.db'}"
    init_db(database_url).dispose()
    provider_result = get_live_discovery_provider("mock").discover(
        LiveDiscoveryRequest(
            country="United States",
            disease="influenza",
            provider="mock",
            metadata={
                "fixture": {
                    "results": [
                        {
                            "title": "Mock influenza dataset",
                            "url": "https://fixture.invalid/influenza.csv",
                            "snippet": "CSV influenza surveillance dataset for local provider-result testing.",
                            "source_name": "Mock Influenza CSV",
                            "rank": 1,
                        }
                    ]
                }
            },
        )
    )

    with get_session(database_url) as session:
        report = SourceDiscoveryAgent(session).discover_from_provider_result(
            country="United States",
            disease="influenza",
            provider_result=provider_result,
            min_source_score=0.0,
        )
        sources = session.query(SourceRegistry).all()

    assert report.status == "ok"
    assert report.candidates_found == 1
    assert all(source.status == "pending_review" for source in sources)
