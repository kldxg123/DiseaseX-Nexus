from __future__ import annotations

from abc import ABC, abstractmethod
from urllib.parse import urlparse

import httpx

from deepseek_web_search_agent.config import DeepSeekWebSearchConfig, get_config
from deepseek_web_search_agent.types import GeneralWebSearchRequest, GeneralWebSearchResult
from web_search_providers.registry import get_web_search_provider
from web_search_providers.types import WebSearchRequest


class GeneralWebSearchProvider(ABC):
    name = "base"

    @abstractmethod
    def search(self, request: GeneralWebSearchRequest) -> list[GeneralWebSearchResult]:
        raise NotImplementedError


class MockSearchProvider(GeneralWebSearchProvider):
    name = "mock"

    def search(self, request: GeneralWebSearchRequest) -> list[GeneralWebSearchResult]:
        disease = request.disease.strip() or "disease"
        country = request.country.strip() or "country"
        rows = [
            (
                f"{country} {disease} weekly surveillance CSV dataset",
                f"https://public-health.example.org/{_slug(disease)}/weekly-surveillance.csv",
                f"Weekly {disease} surveillance data for {country}; CSV table with cases by week.",
                "csv_dataset",
            ),
            (
                f"{country} {disease} public health dashboard",
                f"https://health.example.gov/{_slug(disease)}/dashboard",
                f"Official dashboard for {disease} activity in {country}; charts and weekly updates.",
                "dashboard",
            ),
            (
                f"{country} {disease} outbreak report",
                f"https://www.example.edu/public-health/{_slug(disease)}-outbreak-report",
                f"Public health report mentioning 12 {disease} cases in {country} during 2026-W10.",
                "official_report_page",
            ),
        ]
        return [
            GeneralWebSearchResult(
                query=request.query,
                rank=index + 1,
                title=title,
                url=url,
                domain=normalize_domain(url),
                snippet=snippet,
                source_type_hint=hint,
                provider=self.name,
                safe_metadata={"mock": True},
            )
            for index, (title, url, snippet, hint) in enumerate(rows[: request.max_results])
        ]


class _HttpSearchProvider(GeneralWebSearchProvider):
    endpoint = ""
    api_key_name = ""

    def __init__(
        self,
        config: DeepSeekWebSearchConfig | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.config = config or get_config()
        self.transport = transport

    @property
    def api_key(self) -> str | None:
        return self.config.provider_api_keys.get(self.name)

    def search(self, request: GeneralWebSearchRequest) -> list[GeneralWebSearchResult]:
        if not request.allow_network:
            return [_error_result(request, self.name, "not_authorized")]
        if not self.api_key:
            return [_error_result(request, self.name, "missing_api_key")]
        try:
            return self._search_live(request)
        except httpx.TimeoutException:
            return [_error_result(request, self.name, "timeout")]
        except Exception:
            return [_error_result(request, self.name, "parse_error")]

    def _search_live(self, request: GeneralWebSearchRequest) -> list[GeneralWebSearchResult]:
        raise NotImplementedError


class BingSearchProvider(_HttpSearchProvider):
    name = "bing"

    def _search_live(self, request: GeneralWebSearchRequest) -> list[GeneralWebSearchResult]:
        with httpx.Client(timeout=20.0, transport=self.transport) as client:
            response = client.get(
                "https://api.bing.microsoft.com/v7.0/search",
                headers={"Ocp-Apim-Subscription-Key": self.api_key or ""},
                params={"q": request.query, "count": request.max_results},
            )
        if response.status_code == 429:
            return [_error_result(request, self.name, "rate_limited")]
        if response.status_code == 403:
            return [_error_result(request, self.name, "forbidden")]
        if response.status_code >= 400:
            return [_error_result(request, self.name, "http_error")]
        values = response.json().get("webPages", {}).get("value", [])
        return _results_from_items(request, self.name, values, title_key="name", url_key="url", snippet_key="snippet")


class BraveSearchProvider(_HttpSearchProvider):
    name = "brave"

    def _search_live(self, request: GeneralWebSearchRequest) -> list[GeneralWebSearchResult]:
        with httpx.Client(timeout=20.0, transport=self.transport) as client:
            response = client.get(
                "https://api.search.brave.com/res/v1/web/search",
                headers={"X-Subscription-Token": self.api_key or ""},
                params={"q": request.query, "count": request.max_results},
            )
        if response.status_code == 429:
            return [_error_result(request, self.name, "rate_limited")]
        if response.status_code == 403:
            return [_error_result(request, self.name, "forbidden")]
        if response.status_code >= 400:
            return [_error_result(request, self.name, "http_error")]
        values = response.json().get("web", {}).get("results", [])
        return _results_from_items(request, self.name, values, title_key="title", url_key="url", snippet_key="description")


class SerpApiSearchProvider(_HttpSearchProvider):
    name = "serpapi"

    def _search_live(self, request: GeneralWebSearchRequest) -> list[GeneralWebSearchResult]:
        with httpx.Client(timeout=20.0, transport=self.transport) as client:
            response = client.get(
                "https://serpapi.com/search.json",
                params={"q": request.query, "api_key": self.api_key, "num": request.max_results},
            )
        if response.status_code == 429:
            return [_error_result(request, self.name, "rate_limited")]
        if response.status_code >= 400:
            return [_error_result(request, self.name, "http_error")]
        values = response.json().get("organic_results", [])
        return _results_from_items(request, self.name, values, title_key="title", url_key="link", snippet_key="snippet")


class TavilySearchProvider(_HttpSearchProvider):
    name = "tavily"

    def _search_live(self, request: GeneralWebSearchRequest) -> list[GeneralWebSearchResult]:
        provider = get_web_search_provider("tavily", api_key=self.api_key, transport=self.transport)
        provider_result = provider.search(
            WebSearchRequest(
                query=request.query,
                provider_name="tavily",
                max_results=request.max_results,
                allow_network=request.allow_network,
                country=request.country,
                disease=request.disease,
            )
        )
        if provider_result.failure_type and not provider_result.results:
            return [_error_result(request, self.name, provider_result.failure_type)]
        rows: list[GeneralWebSearchResult] = []
        for item in provider_result.results:
            rows.append(
                GeneralWebSearchResult(
                    query=item.query,
                    rank=item.rank,
                    title=item.title,
                    url=item.url,
                    domain=item.domain,
                    snippet=item.snippet[:500],
                    published_date=item.published_date,
                    source_type_hint=item.source_type_hint,
                    provider=self.name,
                    safe_metadata={"live_search": True},
                )
            )
        return rows or [_error_result(request, self.name, "no_results")]


class ExaSearchProvider(_HttpSearchProvider):
    name = "exa"

    def _search_live(self, request: GeneralWebSearchRequest) -> list[GeneralWebSearchResult]:
        with httpx.Client(timeout=20.0, transport=self.transport) as client:
            response = client.post(
                "https://api.exa.ai/search",
                headers={"x-api-key": self.api_key or ""},
                json={"query": request.query, "numResults": request.max_results},
            )
        if response.status_code == 429:
            return [_error_result(request, self.name, "rate_limited")]
        if response.status_code >= 400:
            return [_error_result(request, self.name, "http_error")]
        values = response.json().get("results", [])
        return _results_from_items(request, self.name, values, title_key="title", url_key="url", snippet_key="text")


PROVIDER_CLASSES = {
    "mock": MockSearchProvider,
    "bing": BingSearchProvider,
    "brave": BraveSearchProvider,
    "serpapi": SerpApiSearchProvider,
    "tavily": TavilySearchProvider,
    "exa": ExaSearchProvider,
}


def get_search_provider(
    name: str,
    config: DeepSeekWebSearchConfig | None = None,
    *,
    transport: httpx.BaseTransport | None = None,
) -> GeneralWebSearchProvider:
    normalized = str(name or "mock").strip().lower()
    cls = PROVIDER_CLASSES.get(normalized, MockSearchProvider)
    if cls is MockSearchProvider:
        return cls()
    return cls(config=config, transport=transport)


def normalize_domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def _results_from_items(
    request: GeneralWebSearchRequest,
    provider: str,
    items: list,
    *,
    title_key: str,
    url_key: str,
    snippet_key: str,
) -> list[GeneralWebSearchResult]:
    rows: list[GeneralWebSearchResult] = []
    for index, item in enumerate(list(items)[: request.max_results]):
        if not isinstance(item, dict):
            continue
        url = str(item.get(url_key) or "").strip()
        if not url:
            continue
        rows.append(
            GeneralWebSearchResult(
                query=request.query,
                rank=index + 1,
                title=str(item.get(title_key) or ""),
                url=url,
                domain=normalize_domain(url),
                snippet=str(item.get(snippet_key) or "")[:500],
                published_date=item.get("date") or item.get("published_date"),
                source_type_hint=item.get("source_type_hint"),
                provider=provider,
                safe_metadata={"live_search": True},
            )
        )
    return rows or [_error_result(request, provider, "no_results")]


def _error_result(request: GeneralWebSearchRequest, provider: str, error_type: str) -> GeneralWebSearchResult:
    return GeneralWebSearchResult(
        query=request.query,
        rank=0,
        title=f"{provider} search skipped or failed",
        url=f"https://search.local/{provider}/{error_type}",
        domain="search.local",
        snippet=f"Structured search status: {error_type}.",
        provider=provider,
        safe_metadata={"error_type": error_type},
        error_type=error_type,
    )


def _slug(value: str) -> str:
    return "-".join(part for part in value.lower().replace("_", " ").split() if part) or "disease"
