from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class WebSearchRequest:
    query: str
    provider_name: str = "tavily"
    max_results: int = 8
    search_depth: str = "basic"
    include_raw_content: bool = False
    allow_network: bool = False
    country: str | None = None
    disease: str | None = None
    domain_filter: list[str] = field(default_factory=list)
    time_range: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class WebSearchResult:
    provider_name: str
    query: str
    title: str
    url: str
    domain: str
    snippet: str
    rank: int = 0
    published_date: str | None = None
    source_type_hint: str | None = None
    safe_metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class WebPageReadRequest:
    url: str
    provider_name: str = "http"
    allow_network: bool = False
    max_bytes: int = 200_000
    follow_links: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class WebPageReadResult:
    provider_name: str
    url: str
    status: str
    status_code: int | None = None
    content_type: str | None = None
    title: str | None = None
    text_summary: str | None = None
    request_count: int = 0
    network_accessed: bool = False
    failure_type: str | None = None
    sanitized_error_summary: str | None = None
    hints: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class WebSearchProviderResult:
    provider_name: str
    query: str
    results: list[WebSearchResult] = field(default_factory=list)
    request_count: int = 0
    network_accessed: bool = False
    failure_type: str | None = None
    sanitized_error_summary: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["results"] = [result.to_dict() for result in self.results]
        return payload


@dataclass(frozen=True)
class WebSearchProviderCapabilities:
    provider_name: str
    supports_search: bool
    supports_extract: bool = False
    supports_crawl: bool = False
    supports_native_web_search: bool = False
    supports_citations: bool = False
    requires_api_key: bool = True
    supports_domain_filter: bool = False
    supports_time_range: bool = False
    supports_country_filter: bool = False
    max_safe_results: int = 8
    implemented_live: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
