from __future__ import annotations

import httpx

from source_discovery_agent.live_providers.data_gov_provider import DataGovLiveDiscoveryProvider
from source_discovery_agent.live_providers.types import LiveDiscoveryRequest


def test_data_gov_provider_mock_transport_single_request():
    seen = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["count"] += 1
        return httpx.Response(
            200,
            json={
                "result": {
                    "results": [
                        {
                            "name": "cholera-surveillance",
                            "title": "Cholera surveillance dataset",
                            "notes": "United States cholera surveillance CSV data.",
                            "organization": {"title": "CDC"},
                            "resources": [{"format": "CSV"}],
                            "metadata_modified": "2025-01-01",
                        }
                    ]
                }
            },
        )

    result = DataGovLiveDiscoveryProvider().discover(
        LiveDiscoveryRequest(
            country="United States",
            disease="cholera",
            provider="data_gov",
            allow_network=True,
            metadata={"execute_live": True, "transport": httpx.MockTransport(handler)},
        )
    )
    assert seen["count"] == 1
    assert result.request_count == 1
    assert result.search_results[0]["title"] == "Cholera surveillance dataset"
    assert result.network_accessed is True
