from __future__ import annotations

import os

import httpx

from web_search_providers.bing_provider import BingWebSearchProvider
from web_search_providers.brave_provider import BraveWebSearchProvider
from web_search_providers.exa_provider import ExaWebSearchProvider
from web_search_providers.africa_cdc_provider import AfricaCdcSearchProvider
from web_search_providers.hdx_provider import HdxSearchProvider
from web_search_providers.mock_provider import MockWebSearchProvider
from web_search_providers.openai_web_search_provider import OpenAIWebSearchProvider
from web_search_providers.perplexity_provider import PerplexityWebSearchProvider
from web_search_providers.reliefweb_provider import ReliefWebSearchProvider
from web_search_providers.serpapi_provider import SerpApiWebSearchProvider
from web_search_providers.tavily_provider import TavilyWebSearchProvider
from web_search_providers.who_provider import WhoSearchProvider
from web_search_providers.who_afro_provider import WhoAfroSearchProvider

PROVIDER_NAMES = (
    "tavily",
    "mock",
    "openai_web_search",
    "perplexity",
    "bing",
    "brave",
    "exa",
    "serpapi",
    "reliefweb",
    "hdx",
    "who",
    "who_afro",
    "africa_cdc",
)


def get_web_search_provider(
    name: str | None = None,
    *,
    api_key: str | None = None,
    transport: httpx.BaseTransport | None = None,
):
    normalized = str(name or "tavily").strip().lower() or "tavily"
    aliases = {
        "openai": "openai_web_search",
        "openai_web": "openai_web_search",
        "serp_api": "serpapi",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized == "mock":
        return MockWebSearchProvider()
    if normalized == "tavily":
        return TavilyWebSearchProvider(api_key=api_key or os.getenv("TAVILY_API_KEY"), transport=transport)
    if normalized == "openai_web_search":
        return OpenAIWebSearchProvider()
    if normalized == "perplexity":
        return PerplexityWebSearchProvider()
    if normalized == "bing":
        return BingWebSearchProvider()
    if normalized == "brave":
        return BraveWebSearchProvider()
    if normalized == "exa":
        return ExaWebSearchProvider()
    if normalized == "serpapi":
        return SerpApiWebSearchProvider()
    if normalized == "reliefweb":
        return ReliefWebSearchProvider()
    if normalized == "hdx":
        return HdxSearchProvider()
    if normalized == "who":
        return WhoSearchProvider()
    if normalized == "who_afro":
        return WhoAfroSearchProvider()
    if normalized == "africa_cdc":
        return AfricaCdcSearchProvider()
    return MockWebSearchProvider()


def list_web_search_providers() -> list[dict]:
    return [get_web_search_provider(name).capabilities.to_dict() for name in PROVIDER_NAMES]
