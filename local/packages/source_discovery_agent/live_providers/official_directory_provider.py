from __future__ import annotations

import hashlib
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx

from source_discovery_agent.live_providers.types import (
    LiveDiscoveryProviderResult,
    LiveDiscoveryRequest,
    sanitize_search_result,
)

DEFAULT_CURATED_RESULTS = [
    {
        "title": "CDC FluView influenza surveillance",
        "url": "https://www.cdc.gov/flu/weekly/fluviewinteractive.htm",
        "snippet": "Curated official directory readiness entry for influenza surveillance dashboard review.",
        "source_name": "CDC FluView",
        "rank": 1,
    },
    {
        "title": "WHO influenza updates",
        "url": "https://www.who.int/teams/global-influenza-programme/surveillance-and-monitoring/influenza-updates",
        "snippet": "Curated official directory readiness entry for influenza update page review.",
        "source_name": "WHO Influenza Updates",
        "rank": 2,
    },
]


class OfficialDirectoryLiveDiscoveryProvider:
    provider_name = "official_directory"

    def discover(self, request: LiveDiscoveryRequest) -> LiveDiscoveryProviderResult:
        if request.metadata.get("execute_live") is True:
            return self.discover_live(
                request,
                transport=request.metadata.get("transport"),
                timeout_seconds=float(request.metadata.get("timeout_seconds", 20.0)),
            )
        payload = request.metadata.get("curated_results") or DEFAULT_CURATED_RESULTS
        results = payload if isinstance(payload, list) else []
        warnings = [
            "Official directory provider used curated local entries only.",
            "No network request was executed.",
        ]
        if request.allow_network:
            warnings.append("allow_network was supplied, but readiness providers still use dry-run data.")
        return LiveDiscoveryProviderResult(
            provider=self.provider_name,
            request_count=0,
            search_results=[sanitize_search_result(item) for item in results if isinstance(item, dict)],
            warnings=warnings,
            network_accessed=False,
            metadata={"curated_results": len(results)},
        )

    def discover_live(
        self,
        request: LiveDiscoveryRequest,
        *,
        transport=None,
        timeout_seconds: float = 20.0,
    ) -> LiveDiscoveryProviderResult:
        if not request.allow_network:
            return LiveDiscoveryProviderResult(
                provider=self.provider_name,
                request_count=0,
                search_results=[],
                warnings=["Official directory live execution refused because allow_network is false."],
                network_accessed=False,
                metadata={},
                failure_type="policy_denied",
                retry_allowed=False,
            )
        directory_url = str(request.metadata.get("directory_url") or "").strip()
        if not directory_url:
            return LiveDiscoveryProviderResult(
                provider=self.provider_name,
                request_count=0,
                search_results=[],
                warnings=["Official directory live execution requires a human-reviewed directory_url."],
                network_accessed=False,
                metadata={},
                failure_type="invalid_provider_config",
                retry_allowed=False,
            )
        try:
            with httpx.Client(
                follow_redirects=False,
                timeout=float(timeout_seconds),
                transport=transport,
                headers={"User-Agent": "infectious-intel-local-source-discovery/0.1"},
            ) as client:
                response = client.get(directory_url)
        except httpx.TimeoutException as exc:
            return LiveDiscoveryProviderResult(
                provider=self.provider_name,
                request_count=1,
                search_results=[],
                warnings=[f"Official directory request timed out safely: {type(exc).__name__}."],
                network_accessed=True,
                metadata={"request_url_domain": urlsplit(directory_url).hostname},
                failure_type="timeout",
                retry_allowed=False,
            )
        except httpx.HTTPError as exc:
            return LiveDiscoveryProviderResult(
                provider=self.provider_name,
                request_count=1,
                search_results=[],
                warnings=[f"Official directory request failed safely: {type(exc).__name__}."],
                network_accessed=True,
                metadata={"request_url_domain": urlsplit(directory_url).hostname},
                failure_type="http_error",
                retry_allowed=False,
            )

        content = response.text or ""
        metadata = {
            "status_code": int(response.status_code),
            "content_type": response.headers.get("content-type"),
            "content_length": len(response.content or b""),
            "content_hash_prefix": hashlib.sha256(response.content or b"").hexdigest()[:16],
            "request_url_domain": urlsplit(directory_url).hostname,
        }
        if response.status_code >= 400:
            failure_type = _failure_type_for_status(response.status_code)
            return LiveDiscoveryProviderResult(
                provider=self.provider_name,
                request_count=1,
                search_results=[],
                warnings=[f"Official directory request returned HTTP {response.status_code}."],
                network_accessed=True,
                metadata=metadata,
                failure_type=failure_type,
                retry_allowed=False,
            )
        try:
            results = parse_official_directory_html(content, base_url=directory_url)
        except Exception as exc:
            return LiveDiscoveryProviderResult(
                provider=self.provider_name,
                request_count=1,
                search_results=[],
                warnings=[f"Official directory parser failed safely: {type(exc).__name__}."],
                network_accessed=True,
                metadata=metadata,
                failure_type="parse_error",
                retry_allowed=False,
            )
        warnings = [] if results else ["Official directory page contained no parseable candidate links."]
        return LiveDiscoveryProviderResult(
            provider=self.provider_name,
            request_count=1,
            search_results=results,
            warnings=warnings,
            network_accessed=True,
            metadata=metadata,
            failure_type=None if results else "no_results",
            retry_allowed=False,
        )


def parse_official_directory_html(content: str, *, base_url: str) -> list[dict]:
    parser = _DirectoryLinkParser()
    parser.feed(content or "")
    results: list[dict] = []
    seen: set[str] = set()
    for link in parser.links:
        href = str(link.get("href") or "").strip()
        title = _squash(link.get("text") or "")
        if not href or not title:
            continue
        if _is_navigation_title(title):
            continue
        if any(ch.isspace() for ch in href):
            continue
        if href.startswith("#") or href.lower().startswith(("mailto:", "javascript:")):
            continue
        try:
            absolute = urljoin(base_url, href)
            parsed = urlsplit(absolute)
        except ValueError:
            continue
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            continue
        normalized = absolute.split("#", 1)[0]
        if normalized in seen:
            continue
        seen.add(normalized)
        results.append(
            sanitize_search_result(
                {
                    "title": title[:160],
                    "url": normalized,
                    "snippet": _snippet(title=title, context=link.get("context")),
                    "source_name": title[:120],
                    "rank": len(results) + 1,
                }
            )
        )
    return results


class _DirectoryLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[dict] = []
        self._active_href: str | None = None
        self._active_parts: list[str] = []
        self._context_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() == "a":
            attrs_dict = {str(key).lower(): str(value or "") for key, value in attrs}
            self._active_href = attrs_dict.get("href")
            self._active_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._active_href is not None:
            self.links.append(
                {
                    "href": self._active_href,
                    "text": _squash(" ".join(self._active_parts)),
                    "context": _squash(" ".join(self._context_parts[-10:])),
                }
            )
            self._active_href = None
            self._active_parts = []

    def handle_data(self, data: str) -> None:
        text = _squash(data)
        if not text:
            return
        self._context_parts.append(text)
        if len(self._context_parts) > 40:
            self._context_parts = self._context_parts[-40:]
        if self._active_href is not None:
            self._active_parts.append(text)


def _snippet(*, title: str, context: str | None) -> str:
    text = _squash(f"{title}. {context or ''}")
    return text[:220]


def _is_navigation_title(title: str) -> bool:
    normalized = _squash(title).strip().lower()
    blocked_exact = {
        "view more",
        "view all",
        "contact us",
        "sign up for email updates",
        "home",
        "about",
        "about cdc",
        "search",
        "menu",
        "more",
        "privacy",
        "accessibility",
    }
    if normalized in blocked_exact:
        return True
    return normalized.startswith(("view more", "view all", "sign up", "contact ", "email updates", "about cdc"))


def _squash(value: object) -> str:
    return " ".join(str(value or "").split())


def _failure_type_for_status(status_code: int) -> str:
    if int(status_code) == 429:
        return "rate_limited"
    if int(status_code) == 403:
        return "forbidden"
    if int(status_code) == 404:
        return "not_found"
    return "http_error"
