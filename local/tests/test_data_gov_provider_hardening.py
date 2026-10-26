from __future__ import annotations

import json

import httpx

from source_discovery_agent.live_providers.data_gov_provider import (
    build_data_gov_query,
    build_data_gov_url,
    DataGovLiveDiscoveryProvider,
)
from source_discovery_agent.live_providers.types import LiveDiscoveryRequest


def _request(transport: httpx.MockTransport) -> LiveDiscoveryRequest:
    return LiveDiscoveryRequest(
        country="United States",
        disease="measles",
        provider="data_gov",
        allow_network=True,
        terms_reviewed=True,
        manual_reviewed=True,
        metadata={"execute_live": True, "transport": transport, "timeout_seconds": 5},
    )


def test_data_gov_query_uses_aliases_and_dataset_terms():
    query = build_data_gov_query("United States", "measles").lower()
    assert "measles" in query
    assert "rubeola" in query
    assert "surveillance" in query
    assert "dataset" in query
    assert "cases" in query
    assert "united states" in query
    assert "sort=" not in build_data_gov_url(query=query)


def test_data_gov_provider_classifies_http_failures():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "too many"})

    result = DataGovLiveDiscoveryProvider().discover(_request(httpx.MockTransport(handler)))

    assert result.request_count == 1
    assert result.network_accessed is True
    assert result.failure_type == "rate_limited"
    assert result.search_results == []
    assert json.dumps(result.metadata).lower().find("too many") == -1


def test_data_gov_provider_classifies_parse_error():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>not json</html>")

    result = DataGovLiveDiscoveryProvider().discover(_request(httpx.MockTransport(handler)))

    assert result.failure_type == "parse_error"
    assert result.request_count == 1
    assert result.search_results == []


def test_data_gov_provider_handles_empty_success():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"result": {"results": []}})

    result = DataGovLiveDiscoveryProvider().discover(_request(httpx.MockTransport(handler)))

    assert result.failure_type == "no_results"
    assert result.request_count == 1
    assert result.search_results == []
