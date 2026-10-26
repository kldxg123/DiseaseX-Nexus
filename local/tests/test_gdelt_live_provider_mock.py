from __future__ import annotations

import httpx

from source_discovery_agent.live_providers import LiveDiscoveryRequest, get_live_discovery_provider


def test_gdelt_live_provider_execute_live_uses_one_mocked_request() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(
            200,
            json={
                "articles": [
                    {
                        "title": "United States influenza surveillance data dashboard",
                        "url": "https://health.example.gov/influenza/surveillance",
                        "domain": "health.example.gov",
                        "sourcecountry": "US",
                        "language": "English",
                        "seendate": "20260705T090000Z",
                    }
                ]
            },
        )

    result = get_live_discovery_provider("gdelt").discover(
        LiveDiscoveryRequest(
            country="United States",
            disease="influenza",
            provider="gdelt",
            allow_network=True,
            terms_reviewed=True,
            manual_reviewed=True,
            metadata={"execute_live": True, "transport": httpx.MockTransport(handler)},
        )
    )

    assert len(calls) == 1
    assert result.request_count == 1
    assert result.network_accessed is True
    assert len(result.search_results) == 1
    assert result.search_results[0]["source_name"] == "health.example.gov"
    assert "api.gdeltproject.org" in calls[0]


def test_gdelt_live_provider_refuses_execute_without_allow_network() -> None:
    result = get_live_discovery_provider("gdelt").discover(
        LiveDiscoveryRequest(
            country="United States",
            disease="influenza",
            provider="gdelt",
            allow_network=False,
            metadata={"execute_live": True},
        )
    )

    assert result.request_count == 0
    assert result.network_accessed is False
    assert result.search_results == []
