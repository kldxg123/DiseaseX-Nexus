from __future__ import annotations

import os
from urllib.parse import urlparse

import httpx

from web_search_providers.provider_policy import clamp_request, sanitize_provider_error
from web_search_providers.types import (
    WebSearchProviderCapabilities,
    WebSearchProviderResult,
    WebSearchRequest,
    WebSearchResult,
)


class TavilyWebSearchProvider:
    name = "tavily"
    capabilities = WebSearchProviderCapabilities(
        provider_name="tavily",
        supports_search=True,
        supports_extract=False,
        supports_crawl=False,
        supports_native_web_search=False,
        supports_citations=False,
        requires_api_key=True,
        supports_domain_filter=True,
        supports_time_range=False,
        supports_country_filter=False,
        max_safe_results=8,
        implemented_live=True,
    )

    def __init__(self, api_key: str | None = None, *, transport: httpx.BaseTransport | None = None) -> None:
        self.api_key = api_key or os.getenv("TAVILY_API_KEY")
        self.transport = transport

    def search(self, request: WebSearchRequest) -> WebSearchProviderResult:
        req = clamp_request(request)
        if not req.allow_network:
            return _failed(req, "not_authorized", "Live network search was not authorized.", network=False)
        if not self.api_key:
            return _failed(req, "missing_api_key", "Tavily API key is not configured.", network=False)
        try:
            with httpx.Client(timeout=20.0, transport=self.transport) as client:
                response = client.post(
                    "https://api.tavily.com/search",
                    json={
                        "api_key": self.api_key,
                        "query": req.query,
                        "max_results": req.max_results,
                        "search_depth": req.search_depth,
                        "include_raw_content": False,
                    },
                )
        except httpx.TimeoutException:
            return _failed(req, "timeout", "Tavily request timed out.", network=True)
        except Exception as exc:
            return _failed(req, "provider_error", sanitize_provider_error(exc), network=True)
        if response.status_code == 429:
            return _failed(req, "rate_limited", "Tavily returned rate limiting.", network=True)
        if response.status_code == 403:
            return _failed(req, "forbidden", "Tavily request was forbidden.", network=True)
        if response.status_code >= 400:
            return _failed(req, "http_error", f"Tavily returned HTTP {response.status_code}.", network=True)
        try:
            values = response.json().get("results", [])
        except Exception:
            return _failed(req, "parse_error", "Tavily response could not be parsed.", network=True)
        results: list[WebSearchResult] = []
        for index, item in enumerate(list(values)[: req.max_results]):
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            if not url:
                continue
            results.append(
                WebSearchResult(
                    provider_name=self.name,
                    query=req.query,
                    rank=index + 1,
                    title=str(item.get("title") or "")[:220],
                    url=url[:500],
                    domain=urlparse(url).netloc.lower(),
                    snippet=str(item.get("content") or "")[:500],
                    safe_metadata={"live_search": True},
                )
            )
        return WebSearchProviderResult(
            provider_name=self.name,
            query=req.query,
            results=results,
            request_count=1,
            network_accessed=True,
            failure_type=None if results else "no_results",
            sanitized_error_summary=None if results else "Tavily returned no usable results.",
        )


def _failed(
    request: WebSearchRequest,
    failure_type: str,
    summary: str,
    *,
    network: bool,
) -> WebSearchProviderResult:
    return WebSearchProviderResult(
        provider_name="tavily",
        query=request.query,
        results=[],
        request_count=1 if network else 0,
        network_accessed=network,
        failure_type=failure_type,
        sanitized_error_summary=sanitize_provider_error(summary),
    )
