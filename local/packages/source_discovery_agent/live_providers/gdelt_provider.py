from __future__ import annotations

import hashlib
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx

from source_discovery_agent.live_providers.cooldown import recommend_provider_cooldown
from source_discovery_agent.live_providers.types import LiveDiscoveryProviderResult, LiveDiscoveryRequest
from source_discovery_agent.query_builder import build_discovery_queries

GDELT_DOC_API = "https://api.gdeltproject.org/api/v2/doc/doc"


class GdeltLiveDiscoveryProvider:
    provider_name = "gdelt"

    def discover(self, request: LiveDiscoveryRequest) -> LiveDiscoveryProviderResult:
        query = request.query_text or build_gdelt_query(request.country, request.disease)
        max_records = int(request.metadata.get("max_records", 25) or 25)
        request_url = build_gdelt_url(query=query, max_records=max_records)
        if request.metadata.get("execute_live") is True:
            return _execute_single_request(request, url=request_url)
        warnings = [
            "GDELT provider readiness generated a query and URL template only.",
            "Live discovery execution is not enabled in this readiness step.",
            "No network request was executed.",
            f"Dry-run endpoint domain: {urlsplit(request_url).hostname}",
        ]
        if request.allow_network:
            warnings.append("allow_network was supplied, but readiness providers still refuse live execution.")
        return LiveDiscoveryProviderResult(
            provider=self.provider_name,
            request_count=0,
            search_results=[],
            warnings=warnings,
            network_accessed=False,
            metadata={"dry_run_url_domain": urlsplit(request_url).hostname, "query_length": len(query)},
        )


def build_gdelt_query(country: str, disease: str) -> str:
    queries = build_discovery_queries(country, disease)
    return queries[0].query_text if queries else f"{country} {disease} surveillance data"


def build_gdelt_url(*, query: str, max_records: int = 25) -> str:
    params = {
        "query": query,
        "mode": "ArtList",
        "format": "json",
        "maxrecords": max(1, min(int(max_records), 25)),
    }
    return f"{GDELT_DOC_API}?{urlencode(params)}"


def _execute_single_request(request: LiveDiscoveryRequest, *, url: str) -> LiveDiscoveryProviderResult:
    if not request.allow_network:
        return LiveDiscoveryProviderResult(
            provider="gdelt",
            request_count=0,
            search_results=[],
            warnings=["GDELT live execution refused because allow_network is false."],
            network_accessed=False,
            metadata={"request_url_domain": urlsplit(url).hostname},
                failure_type="policy_denied",
                retry_allowed=False,
                cooldown_recommended=False,
            )
    transport = request.metadata.get("transport")
    try:
        with httpx.Client(
            follow_redirects=False,
            timeout=float(request.metadata.get("timeout_seconds", 20.0)),
            transport=transport,
            headers={"User-Agent": "infectious-intel-local-source-discovery/0.1"},
        ) as client:
            response = client.get(url)
        response_bytes = response.content or b""
        metadata = {
            "status_code": int(response.status_code),
            "content_type": response.headers.get("content-type"),
            "content_length": len(response_bytes),
            "content_hash_prefix": hashlib.sha256(response_bytes).hexdigest()[:16],
            "request_url_domain": urlsplit(url).hostname,
        }
        if response.status_code >= 400:
            failure_type = _failure_type_for_status(response.status_code)
            retry_after_seconds = _retry_after_seconds(response.headers.get("retry-after"))
            cooldown = recommend_provider_cooldown(
                provider="gdelt",
                failure_type=failure_type,
                retry_after_seconds=retry_after_seconds,
            )
            if retry_after_seconds is not None:
                metadata["retry_after_seconds"] = retry_after_seconds
            return LiveDiscoveryProviderResult(
                provider="gdelt",
                request_count=1,
                search_results=[],
                warnings=[f"GDELT request returned HTTP {response.status_code}."],
                network_accessed=True,
                metadata=metadata,
                failure_type=failure_type,
                retry_allowed=False,
                cooldown_recommended=cooldown.cooldown_recommended,
                retry_after_seconds=retry_after_seconds,
            )
        try:
            payload = response.json()
        except ValueError:
            return LiveDiscoveryProviderResult(
                provider="gdelt",
                request_count=1,
                search_results=[],
                warnings=["GDELT response was not valid JSON."],
                network_accessed=True,
                metadata=metadata,
                failure_type="parse_error",
                retry_allowed=False,
                cooldown_recommended=False,
            )
        search_results = _gdelt_payload_to_search_results(payload)
        warnings = [] if search_results else ["GDELT response contained no articles."]
        return LiveDiscoveryProviderResult(
            provider="gdelt",
            request_count=1,
            search_results=search_results,
            warnings=warnings,
            network_accessed=True,
            metadata=metadata,
            failure_type=None if search_results else "no_results",
            retry_allowed=False,
            cooldown_recommended=False,
        )
    except httpx.TimeoutException as exc:
        return LiveDiscoveryProviderResult(
            provider="gdelt",
            request_count=1,
            search_results=[],
            warnings=[f"GDELT request timed out safely: {type(exc).__name__}."],
            network_accessed=True,
            metadata={"request_url_domain": urlsplit(url).hostname},
            failure_type="timeout",
            retry_allowed=False,
            cooldown_recommended=False,
        )
    except httpx.HTTPError as exc:
        return LiveDiscoveryProviderResult(
            provider="gdelt",
            request_count=1,
            search_results=[],
            warnings=[f"GDELT request failed safely: {type(exc).__name__}."],
            network_accessed=True,
            metadata={"request_url_domain": urlsplit(url).hostname},
            failure_type="http_error",
            retry_allowed=False,
            cooldown_recommended=False,
        )


def _gdelt_payload_to_search_results(payload: Any) -> list[dict]:
    if not isinstance(payload, dict):
        return []
    articles = payload.get("articles")
    if not isinstance(articles, list):
        return []
    rows: list[dict] = []
    for index, article in enumerate(articles, start=1):
        if not isinstance(article, dict):
            continue
        url = str(article.get("url") or "").strip()
        if not url:
            continue
        domain = str(article.get("domain") or urlsplit(url).hostname or "").strip()
        title = str(article.get("title") or domain or "GDELT article").strip()
        rows.append(
            {
                "title": title[:180],
                "url": url,
                "snippet": _safe_snippet(article, domain=domain, title=title),
                "source_name": domain or "GDELT candidate",
                "rank": index,
                "published_at": str(article.get("seendate") or article.get("seenDate") or "")[:32] or None,
            }
        )
    return rows


def _safe_snippet(article: dict[str, Any], *, domain: str, title: str) -> str:
    source_country = str(article.get("sourcecountry") or "").strip()
    language = str(article.get("language") or "").strip()
    parts = [
        title,
        f"domain {domain}" if domain else "",
        f"country {source_country}" if source_country else "",
        f"language {language}" if language else "",
        "GDELT article candidate for source discovery.",
    ]
    return " ".join(part for part in parts if part)[:220]


def _retry_after_seconds(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _failure_type_for_status(status_code: int) -> str:
    if int(status_code) == 429:
        return "rate_limited"
    if int(status_code) == 403:
        return "forbidden"
    if int(status_code) == 404:
        return "not_found"
    return "http_error"
