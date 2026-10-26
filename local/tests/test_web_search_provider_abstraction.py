from __future__ import annotations

from web_search_providers import WebSearchRequest, get_web_search_provider, list_web_search_providers


def test_registry_exposes_default_tavily_and_mock_provider() -> None:
    tavily = get_web_search_provider("tavily")
    mock = get_web_search_provider("mock")

    assert tavily.name == "tavily"
    assert tavily.capabilities.supports_search is True
    assert tavily.capabilities.requires_api_key is True
    assert mock.name == "mock"
    assert mock.capabilities.requires_api_key is False


def test_mock_provider_returns_safe_unified_result() -> None:
    provider = get_web_search_provider("mock")
    result = provider.search(
        WebSearchRequest(
            query="influenza United States CSV",
            disease="influenza",
            country="United States",
            max_results=2,
        )
    )

    assert result.provider_name == "mock"
    assert result.network_accessed is False
    assert len(result.results) == 2
    assert result.results[0].source_type_hint in {"csv_dataset", "html_table", "json_api"}


def test_skeleton_providers_do_not_call_live_api() -> None:
    provider_names = {row["provider_name"] for row in list_web_search_providers()}
    assert {"openai_web_search", "perplexity", "bing", "brave", "exa", "serpapi"} <= provider_names

    provider = get_web_search_provider("openai_web_search")
    result = provider.search(WebSearchRequest(query="cholera dataset", allow_network=True))

    assert result.network_accessed is False
    assert result.request_count == 0
    assert result.failure_type == "provider_not_implemented"
