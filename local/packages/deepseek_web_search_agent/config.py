from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


SEARCH_PROVIDER_ENV = {
    "bing": "BING_SEARCH_API_KEY",
    "brave": "BRAVE_SEARCH_API_KEY",
    "serpapi": "SERPAPI_API_KEY",
    "tavily": "TAVILY_API_KEY",
    "exa": "EXA_API_KEY",
    "openai_web_search": "OPENAI_API_KEY",
    "perplexity": "PERPLEXITY_API_KEY",
}


@dataclass(frozen=True)
class DeepSeekWebSearchConfig:
    deepseek_api_key: str | None
    deepseek_base_url: str
    deepseek_model: str
    deepseek_timeout_seconds: float
    general_web_search_provider: str
    provider_api_keys: dict[str, str | None]
    max_search_queries: int
    max_search_results_per_query: int
    max_pages_to_read: int
    max_total_http_requests: int
    max_readable_pages_success: int = 8
    stop_when_structured_candidates_found: bool = True
    prefer_official_and_structured_sources: bool = True

    @property
    def has_deepseek_key(self) -> bool:
        return bool(self.deepseek_api_key)


def get_config() -> DeepSeekWebSearchConfig:
    local = _read_local_env_file()

    def env(name: str, default: str | None = None) -> str | None:
        value = os.getenv(name)
        if value is None:
            value = local.get(name)
        return value if value not in {"", None} else default

    provider_keys = {name: env(env_name) for name, env_name in SEARCH_PROVIDER_ENV.items()}
    return DeepSeekWebSearchConfig(
        deepseek_api_key=env("DEEPSEEK_API_KEY"),
        deepseek_base_url=str(env("DEEPSEEK_BASE_URL", "https://api.deepseek.com")),
        deepseek_model=str(env("DEEPSEEK_MODEL", "deepseek-chat")),
        deepseek_timeout_seconds=float(env("DEEPSEEK_TIMEOUT_SECONDS", "30") or 30),
        general_web_search_provider=str(env("GENERAL_WEB_SEARCH_PROVIDER", "tavily")).strip().lower(),
        provider_api_keys=provider_keys,
        max_search_queries=_env_int(env("MAX_SEARCH_QUERIES"), 4, 1, 6),
        max_search_results_per_query=_env_int(env("MAX_SEARCH_RESULTS_PER_QUERY"), 8, 1, 8),
        max_pages_to_read=_env_int(env("MAX_PAGES_TO_READ"), 8, 0, 8),
        max_total_http_requests=_env_int(env("MAX_TOTAL_HTTP_REQUESTS"), 16, 0, 16),
        max_readable_pages_success=_env_int(env("MAX_READABLE_PAGES_SUCCESS"), 8, 0, 8),
        stop_when_structured_candidates_found=_env_bool(env("STOP_WHEN_STRUCTURED_CANDIDATES_FOUND"), True),
        prefer_official_and_structured_sources=_env_bool(env("PREFER_OFFICIAL_AND_STRUCTURED_SOURCES"), True),
    )


def _read_local_env_file() -> dict[str, str]:
    path = Path(".env")
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return values
    for line in lines:
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, value = text.split("=", 1)
        key = key.strip()
        if not key:
            continue
        values[key] = value.strip().strip('"').strip("'")
    return values


def _env_int(value: str | None, default: int, low: int, high: int) -> int:
    try:
        number = int(value) if value is not None else default
    except ValueError:
        number = default
    return max(low, min(high, number))


def _env_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}
