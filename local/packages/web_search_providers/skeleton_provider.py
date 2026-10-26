from __future__ import annotations

from web_search_providers.provider_policy import clamp_request
from web_search_providers.types import (
    WebSearchProviderCapabilities,
    WebSearchProviderResult,
    WebSearchRequest,
)


class SkeletonWebSearchProvider:
    def __init__(self, name: str, *, native_web_search: bool = False, citations: bool = False) -> None:
        self.name = name
        self.capabilities = WebSearchProviderCapabilities(
            provider_name=name,
            supports_search=True,
            supports_native_web_search=native_web_search,
            supports_citations=citations,
            requires_api_key=True,
            supports_domain_filter=False,
            supports_time_range=False,
            supports_country_filter=False,
            max_safe_results=8,
            implemented_live=False,
        )

    def search(self, request: WebSearchRequest) -> WebSearchProviderResult:
        req = clamp_request(request)
        return WebSearchProviderResult(
            provider_name=self.name,
            query=req.query,
            results=[],
            request_count=0,
            network_accessed=False,
            failure_type="provider_not_implemented",
            sanitized_error_summary=f"{self.name} provider is registered as a safe skeleton only.",
        )
