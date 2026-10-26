from __future__ import annotations

from web_search_providers.registry import (
    get_web_search_provider,
    list_web_search_providers,
)
from web_search_providers.types import (
    WebPageReadRequest,
    WebPageReadResult,
    WebSearchProviderCapabilities,
    WebSearchProviderResult,
    WebSearchRequest,
    WebSearchResult,
)

__all__ = [
    "WebPageReadRequest",
    "WebPageReadResult",
    "WebSearchProviderCapabilities",
    "WebSearchProviderResult",
    "WebSearchRequest",
    "WebSearchResult",
    "get_web_search_provider",
    "list_web_search_providers",
]
