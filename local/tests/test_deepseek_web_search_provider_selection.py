from __future__ import annotations

from deepseek_web_search_agent.config import DeepSeekWebSearchConfig
from deepseek_web_search_agent.search_capability_detector import detect_available_general_search_provider
from deepseek_web_search_agent.web_search_providers import get_search_provider


def _config_with(provider: str | None = None) -> DeepSeekWebSearchConfig:
    keys = {name: None for name in ("bing", "brave", "serpapi", "tavily", "exa")}
    if provider:
        keys[provider] = "placeholder-test-key"
    return DeepSeekWebSearchConfig(
        deepseek_api_key=None,
        deepseek_base_url="https://api.deepseek.com",
        deepseek_model="deepseek-chat",
        deepseek_timeout_seconds=30.0,
        general_web_search_provider="mock",
        provider_api_keys=keys,
        max_search_queries=8,
        max_search_results_per_query=8,
        max_pages_to_read=0,
        max_total_http_requests=6,
    )


def test_provider_classes_exist_for_all_supported_backends() -> None:
    for name in ("mock", "brave", "bing", "serpapi", "tavily", "exa"):
        provider = get_search_provider(name, _config_with())
        assert provider.name == name


def test_provider_selection_uses_requested_key_when_available() -> None:
    for name in ("brave", "bing", "serpapi", "tavily", "exa"):
        capability = detect_available_general_search_provider(_config_with(name), requested_backend=name)
        assert capability.provider == name
        assert capability.status == "available"


def test_provider_selection_falls_back_to_mock_when_key_missing() -> None:
    capability = detect_available_general_search_provider(_config_with(), requested_backend="brave")
    assert capability.provider == "mock"
    assert capability.status == "available"
