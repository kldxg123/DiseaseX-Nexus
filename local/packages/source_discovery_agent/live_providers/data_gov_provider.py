from __future__ import annotations

import hashlib
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx

from source_discovery_agent.live_providers.types import (
    LiveDiscoveryProviderResult,
    LiveDiscoveryRequest,
    sanitize_search_result,
)
DATA_GOV_PACKAGE_SEARCH_API = "https://catalog.data.gov/api/3/action/package_search"

DISEASE_ALIASES = {
    "cholera": ["cholera", "vibrio cholerae"],
    "dengue": ["dengue", "dengue fever"],
    "influenza": ["influenza", "flu", "ili"],
    "measles": ["measles", "rubeola"],
    "norovirus": ["norovirus", "norostat"],
}


class DataGovLiveDiscoveryProvider:
    provider_name = "data_gov"

    def discover(self, request: LiveDiscoveryRequest) -> LiveDiscoveryProviderResult:
        query = request.query_text or build_data_gov_query(request.country, request.disease)
        max_records = int(request.metadata.get("max_records", 10) or 10)
        request_url = build_data_gov_url(query=query, max_records=max_records)
        if request.metadata.get("execute_live") is True:
            return _execute_single_request(request, url=request_url)
        return LiveDiscoveryProviderResult(
            provider=self.provider_name,
            request_count=0,
            search_results=[],
            warnings=[
                "Data.gov provider generated a query and URL template only.",
                "No network request was executed.",
                f"Dry-run endpoint domain: {urlsplit(request_url).hostname}",
            ],
            network_accessed=False,
            metadata={"dry_run_url_domain": urlsplit(request_url).hostname, "query_length": len(query)},
        )


def build_data_gov_query(country: str, disease: str) -> str:
    disease_text = str(disease or "").strip().lower()
    aliases = DISEASE_ALIASES.get(disease_text, [str(disease or "").strip() or "infectious disease"])
    terms = [term for term in aliases[:3] if term]
    terms.extend(["surveillance", "outbreak", "dataset", "cases", str(country or "").strip()])
    return " ".join(term for term in terms if term)


def build_data_gov_url(*, query: str, max_records: int = 10) -> str:
    params = {"q": query, "rows": max(1, min(int(max_records), 20))}
    return f"{DATA_GOV_PACKAGE_SEARCH_API}?{urlencode(params)}"


def _execute_single_request(request: LiveDiscoveryRequest, *, url: str) -> LiveDiscoveryProviderResult:
    if not request.allow_network:
        return LiveDiscoveryProviderResult(
            provider="data_gov",
            request_count=0,
            search_results=[],
            warnings=["Data.gov live execution refused because allow_network is false."],
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
            timeout=float(request.metadata.get("timeout_seconds", 15.0)),
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
            return LiveDiscoveryProviderResult(
                provider="data_gov",
                request_count=1,
                search_results=[],
                warnings=[f"Data.gov package_search returned HTTP {response.status_code}."],
                network_accessed=True,
                metadata=metadata,
                failure_type=failure_type,
                retry_allowed=False,
                cooldown_recommended=failure_type == "rate_limited",
            )
        try:
            payload = response.json()
        except ValueError:
            return LiveDiscoveryProviderResult(
                provider="data_gov",
                request_count=1,
                search_results=[],
                warnings=["Data.gov package_search response was not valid JSON."],
                network_accessed=True,
                metadata=metadata,
                failure_type="parse_error",
                retry_allowed=False,
                cooldown_recommended=False,
            )
        search_results = data_gov_payload_to_search_results(payload)
        warnings = [] if search_results else ["Data.gov package_search returned no datasets for this query."]
        return LiveDiscoveryProviderResult(
            provider="data_gov",
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
            provider="data_gov",
            request_count=1,
            search_results=[],
            warnings=[f"Data.gov package_search timed out safely: {type(exc).__name__}."],
            network_accessed=True,
            metadata={"request_url_domain": urlsplit(url).hostname},
            failure_type="timeout",
            retry_allowed=False,
            cooldown_recommended=False,
        )
    except httpx.HTTPError as exc:
        return LiveDiscoveryProviderResult(
            provider="data_gov",
            request_count=1,
            search_results=[],
            warnings=[f"Data.gov package_search failed safely: {type(exc).__name__}."],
            network_accessed=True,
            metadata={"request_url_domain": urlsplit(url).hostname},
            failure_type="http_error",
            retry_allowed=False,
            cooldown_recommended=False,
        )


def data_gov_payload_to_search_results(payload: Any) -> list[dict]:
    if not isinstance(payload, dict):
        return []
    result = payload.get("result")
    if isinstance(result, dict):
        packages = result.get("results")
    else:
        packages = payload.get("results")
    if not isinstance(packages, list):
        return []
    rows: list[dict] = []
    for package in packages:
        if not isinstance(package, dict):
            continue
        row = _package_to_search_result(package, rank=len(rows) + 1)
        if row:
            rows.append(row)
    return rows


def _package_to_search_result(package: dict[str, Any], *, rank: int) -> dict | None:
    name = str(package.get("name") or "").strip()
    title = str(package.get("title") or name or "Data.gov dataset").strip()
    url = _dataset_url(package, name=name)
    if not url:
        return None
    organization = package.get("organization") if isinstance(package.get("organization"), dict) else {}
    source_name = str(organization.get("title") or organization.get("name") or "Data.gov").strip()
    notes = str(package.get("notes") or "").strip()
    tags = " ".join(_tag_name(tag) for tag in package.get("tags") or [] if isinstance(tag, dict))
    formats = " ".join(_resource_format(resource) for resource in package.get("resources") or [] if isinstance(resource, dict))
    snippet = " ".join(part for part in (notes, f"tags {tags}" if tags else "", f"formats {formats}" if formats else "") if part)
    return sanitize_search_result(
        {
            "title": title[:180],
            "url": url,
            "snippet": snippet[:240] or "Data.gov public dataset candidate for source discovery.",
            "source_name": source_name[:120],
            "rank": rank,
            "published_at": str(package.get("metadata_modified") or package.get("metadata_created") or "")[:32] or None,
        }
    )


def _dataset_url(package: dict[str, Any], *, name: str) -> str:
    for key in ("url", "landing_page"):
        value = str(package.get(key) or "").strip()
        if value.startswith(("http://", "https://")):
            return value
    if name:
        return f"https://catalog.data.gov/dataset/{name}"
    return ""


def _tag_name(tag: dict[str, Any]) -> str:
    return str(tag.get("display_name") or tag.get("name") or "").strip()


def _resource_format(resource: dict[str, Any]) -> str:
    return str(resource.get("format") or resource.get("mimetype") or "").strip()


def _failure_type_for_status(status_code: int) -> str:
    if int(status_code) == 429:
        return "rate_limited"
    if int(status_code) == 403:
        return "forbidden"
    if int(status_code) == 404:
        return "not_found"
    return "http_error"
