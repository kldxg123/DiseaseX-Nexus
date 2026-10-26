from __future__ import annotations

from dataclasses import dataclass

from deepseek_web_search_agent.config import DeepSeekWebSearchConfig, get_config


@dataclass(frozen=True)
class SearchCapability:
    backend: str
    status: str
    provider: str
    warnings: list[str]


def detect_deepseek_native_web_search(config: DeepSeekWebSearchConfig | None = None) -> SearchCapability:
    cfg = config or get_config()
    return SearchCapability(
        backend="deepseek_native_web_search",
        status="unavailable",
        provider="deepseek",
        warnings=[
            "DeepSeek native web search tool is not exposed in this runtime; external provider or mock is required."
        ],
    )


def detect_available_general_search_provider(
    config: DeepSeekWebSearchConfig | None = None,
    *,
    requested_backend: str = "auto",
) -> SearchCapability:
    cfg = config or get_config()
    requested = str(requested_backend or "auto").strip().lower()
    if requested == "mock":
        return SearchCapability("mock_search", "available", "mock", [])
    candidate_order = [requested] if requested not in {"auto", "deepseek_native"} else []
    candidate_order += [cfg.general_web_search_provider, "bing", "brave", "serpapi", "tavily", "exa"]
    seen: set[str] = set()
    for provider in candidate_order:
        provider = str(provider or "").strip().lower()
        if provider in seen or provider in {"", "auto", "mock", "deepseek_native"}:
            continue
        seen.add(provider)
        if cfg.provider_api_keys.get(provider):
            return SearchCapability("external_search_api", "available", provider, [])
    return SearchCapability(
        "mock_search",
        "available",
        "mock",
        ["No configured external web search provider key was found; using mock search."],
    )


def choose_search_backend(
    config: DeepSeekWebSearchConfig | None = None,
    *,
    requested_backend: str = "auto",
) -> SearchCapability:
    requested = str(requested_backend or "auto").strip().lower()
    if requested in {"deepseek_native", "auto"}:
        native = detect_deepseek_native_web_search(config)
        if native.status == "available":
            return native
    return detect_available_general_search_provider(config, requested_backend=requested)
