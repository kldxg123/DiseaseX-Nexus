from __future__ import annotations

from web_search_providers.provider_policy import sanitize_provider_error
from web_search_providers.registry import get_web_search_provider
from web_search_providers.types import WebSearchRequest


def test_provider_errors_are_sanitized() -> None:
    text = sanitize_provider_error("api_key=abc token secret=def password=hunter2")

    lowered = text.lower()
    assert "abc" not in lowered
    assert "hunter2" not in lowered
    assert "password" not in lowered


def test_tavily_without_authorization_does_not_access_network() -> None:
    provider = get_web_search_provider("tavily", api_key=None)
    result = provider.search(WebSearchRequest(query="influenza data", allow_network=False))

    assert result.network_accessed is False
    assert result.request_count == 0
    assert result.failure_type == "not_authorized"
