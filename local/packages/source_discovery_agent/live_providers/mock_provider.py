from __future__ import annotations

from source_discovery_agent.live_providers.types import (
    LiveDiscoveryProviderResult,
    LiveDiscoveryRequest,
    sanitize_search_result,
)


class MockLiveDiscoveryProvider:
    provider_name = "mock"

    def discover(self, request: LiveDiscoveryRequest) -> LiveDiscoveryProviderResult:
        payload = request.metadata.get("fixture") or request.metadata.get("search_results") or {}
        results = payload.get("results", payload) if isinstance(payload, dict) else payload
        if not isinstance(results, list):
            results = []
        return LiveDiscoveryProviderResult(
            provider=self.provider_name,
            request_count=0,
            search_results=[sanitize_search_result(item) for item in results if isinstance(item, dict)],
            warnings=["Mock provider used local fixture data only; no network request was executed."],
            network_accessed=False,
        )
