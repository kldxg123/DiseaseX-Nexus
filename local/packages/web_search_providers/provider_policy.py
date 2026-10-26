from __future__ import annotations

import re
from dataclasses import dataclass

from web_search_providers.types import WebSearchRequest

DEFAULT_MAX_SEARCH_QUERIES = 4
MAX_SEARCH_QUERIES_LIMIT = 6
DEFAULT_MAX_SEARCH_RESULTS_PER_QUERY = 8
DEFAULT_MAX_PAGES_TO_READ = 8
DEFAULT_MAX_TOTAL_HTTP_REQUESTS = 16
DEFAULT_MAX_READABLE_PAGES_SUCCESS = 8

SENSITIVE_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|token|secret|password|credential|authorization)\s*[:=]\s*[^,\s]+"),
    re.compile(r"(?i)bearer\s+[a-z0-9._\-]+"),
)


@dataclass(frozen=True)
class SearchProviderPolicy:
    provider_name: str = "tavily"
    max_results: int = DEFAULT_MAX_SEARCH_RESULTS_PER_QUERY
    allow_network: bool = False
    terms_reviewed: bool = False
    manual_reviewed: bool = False

    @property
    def live_allowed(self) -> bool:
        return bool(self.allow_network and self.terms_reviewed and self.manual_reviewed)


def clamp_request(request: WebSearchRequest) -> WebSearchRequest:
    return WebSearchRequest(
        query=str(request.query or "").strip(),
        provider_name=str(request.provider_name or "tavily").strip().lower() or "tavily",
        max_results=max(1, min(DEFAULT_MAX_SEARCH_RESULTS_PER_QUERY, int(request.max_results or 1))),
        search_depth=str(request.search_depth or "basic"),
        include_raw_content=False,
        allow_network=bool(request.allow_network),
        country=request.country,
        disease=request.disease,
        domain_filter=[str(item) for item in request.domain_filter[:20]],
        time_range=request.time_range,
    )


def sanitize_provider_error(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return "provider_error"
    text = text.replace("\n", " ")[:240]
    for pattern in SENSITIVE_PATTERNS:
        text = pattern.sub("[redacted]", text)
    return text


def assert_live_allowed(policy: SearchProviderPolicy) -> None:
    if not policy.live_allowed:
        raise PermissionError("live_search_not_authorized")
