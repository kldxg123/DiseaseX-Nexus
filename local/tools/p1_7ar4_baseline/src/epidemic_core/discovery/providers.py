from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import urllib.parse
from collections.abc import Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Protocol

from epidemic_core.discovery.safe_fetcher import FetchBlocked, SafeFetcher, canonicalize

REGISTRY_ENV = "DISCOVERY_DIRECTORY_REGISTRY"
BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"


def _sha(value: str | bytes) -> str:
    raw = value if isinstance(value, bytes) else value.encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class DirectoryDefinition:
    directory_id: str
    name: str
    base_url: str
    countries: tuple[str, ...]
    publisher: str
    source_tier: str
    protocol: str
    languages: tuple[str, ...]
    https_required: bool
    enabled: bool
    health_status: str
    last_http_status: int | None
    last_checked_at: str | None
    license_entry: str
    configuration_version: str
    unavailable_reason: str | None

    def supports(self, country_iso3: str, selected_tiers: Sequence[str]) -> bool:
        return self.source_tier in selected_tiers and ("*" in self.countries or country_iso3 in self.countries)

    def evidence(self, required_channel: bool) -> dict[str, object]:
        value = asdict(self)
        value["countries"] = list(self.countries)
        value["languages"] = list(self.languages)
        value["domain"] = urllib.parse.urlsplit(self.base_url).hostname
        value["required_channel"] = required_channel
        value.pop("base_url")
        value.pop("license_entry")
        return value


class DirectoryRegistry:
    def __init__(self, version: str, directories: Sequence[DirectoryDefinition]):
        self.version = version
        self.directories = tuple(directories)

    @classmethod
    def load(cls, path: Path | None = None) -> DirectoryRegistry:
        selected = path or Path(
            os.environ.get(
                REGISTRY_ENV,
                "USER_HOME/autodl-tmp/epidemic-openclaw/config/source_discovery/directories.v1.json",
            )
        )
        value = json.loads(selected.read_text(encoding="utf-8"))
        rows = []
        for item in value["directories"]:
            row = dict(item)
            row["countries"] = tuple(row["countries"])
            row["languages"] = tuple(row["languages"])
            rows.append(DirectoryDefinition(**row))
        return cls(str(value["registry_version"]), rows)

    def matching(self, country_iso3: str, selected_tiers: Sequence[str]) -> tuple[DirectoryDefinition, ...]:
        return tuple(item for item in self.directories if item.supports(country_iso3, selected_tiers))


@dataclass(frozen=True)
class SearchRequest:
    country_iso3: str
    country_iso2: str
    country_name_en: str
    country_name_local: str
    disease_name_en: str
    disease_name_local: str
    desired_metrics: tuple[str, ...]
    time_window_kind: str
    preferred_source_tiers: tuple[str, ...]
    language_preferences: tuple[str, ...]
    maximum_results: int

    def query_text(self) -> str:
        metric_aliases = {
            "cases_new": "new cases weekly cases",
            "deaths_new": "new deaths weekly deaths",
            "incidence_rate": "incidence rate",
        }
        metrics = " ".join(metric_aliases.get(item, item) for item in self.desired_metrics)
        names = " ".join(
            item
            for item in (
                self.country_name_en,
                self.country_iso3,
                self.country_name_local,
                self.disease_name_en,
                self.disease_name_local,
            )
            if item
        )
        return f'{names} {metrics} latest weekly API download "open data" dataset'.strip()

    def summary(self) -> str:
        return (
            f"{self.country_iso3}; {self.disease_name_en}; "
            f"metrics={','.join(self.desired_metrics)}; window={self.time_window_kind}; "
            "API/weekly/latest/download/open data"
        )


@dataclass(frozen=True)
class ProviderCapability:
    provider_id: str
    provider_type: str
    configured: bool
    enabled: bool
    health_status: str
    credential_required: bool
    supported_countries: tuple[str, ...]
    supported_languages: tuple[str, ...]
    last_checked_at: str | None
    unavailable_reason: str | None
    required_channel: bool

    def evidence(self) -> dict[str, object]:
        value = asdict(self)
        value["supported_countries"] = list(self.supported_countries)
        value["supported_languages"] = list(self.supported_languages)
        return value


@dataclass(frozen=True)
class SearchResult:
    canonical_url: str
    title: str
    publisher: str
    description: str
    source_tier: str
    media_type: str | None = None
    license_status: str = "UNKNOWN_REVIEW_REQUIRED"


@dataclass(frozen=True)
class RequestEvidence:
    provider_id: str
    directory_id: str | None
    directory_name: str
    scope: str
    domain: str
    query_summary: str
    query_sha256: str
    network_request: bool
    http_status: int | None
    elapsed_ms: int | None
    result_count: int
    response_sha256: str | None
    result: str
    reason_code: str | None
    affects_completeness: bool

    def event_details(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class ProviderOutcome:
    capability: ProviderCapability
    evidence: RequestEvidence
    results: tuple[SearchResult, ...]


class DiscoveryProvider(Protocol):
    def capability(self, required_channel: bool) -> ProviderCapability: ...

    def search(self, request: SearchRequest, required_channel: bool) -> ProviderOutcome: ...


def _failure_evidence(
    capability: ProviderCapability,
    request: SearchRequest,
    directory_id: str | None,
    directory_name: str,
    domain: str,
    code: str,
    *,
    network_request: bool,
    http_status: int | None = None,
) -> RequestEvidence:
    return RequestEvidence(
        capability.provider_id,
        directory_id,
        directory_name,
        request.country_iso3,
        domain,
        request.summary(),
        _sha(request.query_text()),
        network_request,
        http_status,
        None,
        0,
        None,
        "UNAVAILABLE" if not capability.configured else "FAILED",
        code,
        capability.required_channel,
    )


class BraveSearchProvider:
    def __init__(self, key: str | None = None, fetcher: SafeFetcher | None = None):
        self.key = key if key is not None else os.environ.get("BRAVE_SEARCH_API_KEY", "")
        self.fetcher = fetcher or SafeFetcher(maximum_bytes=4 * 1024 * 1024, timeout_seconds=15)
        self.enabled = os.environ.get("DISCOVERY_BRAVE_ENABLED", "1") == "1"

    def capability(self, required_channel: bool) -> ProviderCapability:
        configured = bool(self.key)
        if not self.enabled:
            health, reason = "DISABLED", "PROVIDER_DISABLED"
        elif not configured:
            health, reason = "MISSING_CREDENTIAL", "BRAVE_SEARCH_API_KEY_NOT_CONFIGURED"
        else:
            health, reason = "READY", None
        return ProviderCapability(
            "brave-web-search-v1",
            "GENERAL_WEB_SEARCH_API",
            configured,
            self.enabled,
            health,
            True,
            ("*",),
            ("*",),
            None,
            reason,
            required_channel,
        )

    def search(self, request: SearchRequest, required_channel: bool) -> ProviderOutcome:
        capability = replace(
            self.capability(required_channel), last_checked_at=dt.datetime.now(dt.timezone.utc).isoformat()
        )
        domain = urllib.parse.urlsplit(BRAVE_ENDPOINT).hostname or ""
        if not capability.enabled or not capability.configured:
            code = capability.unavailable_reason or "PROVIDER_UNAVAILABLE"
            return ProviderOutcome(
                capability,
                _failure_evidence(
                    capability,
                    request,
                    None,
                    "Brave Search API",
                    domain,
                    code,
                    network_request=False,
                ),
                (),
            )
        query = request.query_text()[:400]
        parameters = urllib.parse.urlencode(
            {
                "q": query,
                "count": min(20, request.maximum_results),
                "country": request.country_iso2.lower(),
                "search_lang": (request.language_preferences or ("en",))[0].split("-", 1)[0],
                "safesearch": "strict",
            }
        )
        try:
            fetched = self.fetcher.fetch(
                f"{BRAVE_ENDPOINT}?{parameters}", {"X-Subscription-Token": self.key, "Accept": "application/json"}
            )
            value = json.loads(fetched.payload)
            rows = value.get("web", {}).get("results", [])
            results = []
            for row in rows[: request.maximum_results]:
                url = str(row.get("url") or "")
                try:
                    canonical, _ = canonicalize(url)
                except FetchBlocked:
                    continue
                results.append(
                    SearchResult(
                        canonical,
                        str(row.get("title") or "Untitled dataset")[:500],
                        urllib.parse.urlsplit(canonical).hostname or "unknown publisher",
                        str(row.get("description") or "")[:2000],
                        "GENERAL_WEB_SEARCH",
                    )
                )
            evidence = RequestEvidence(
                capability.provider_id,
                None,
                "Brave Search API",
                request.country_iso3,
                fetched.domain,
                request.summary(),
                _sha(query),
                True,
                fetched.http_status,
                fetched.elapsed_ms,
                len(results),
                fetched.response_sha256,
                "SUCCESS" if results else "NO_RESULTS",
                None,
                required_channel,
            )
            return ProviderOutcome(capability, evidence, tuple(results))
        except (FetchBlocked, json.JSONDecodeError, TypeError, ValueError) as exc:
            code = exc.code if isinstance(exc, FetchBlocked) else "INVALID_PROVIDER_RESPONSE"
            attempted = exc.network_attempted if isinstance(exc, FetchBlocked) else True
            status = exc.http_status if isinstance(exc, FetchBlocked) else None
            return ProviderOutcome(
                capability,
                _failure_evidence(
                    capability,
                    request,
                    None,
                    "Brave Search API",
                    domain,
                    code,
                    network_request=attempted,
                    http_status=status,
                ),
                (),
            )


class DataCiteProvider:
    def __init__(self, directory: DirectoryDefinition, fetcher: SafeFetcher | None = None):
        self.directory = directory
        self.fetcher = fetcher or SafeFetcher(maximum_bytes=8 * 1024 * 1024, timeout_seconds=5)

    def capability(self, required_channel: bool) -> ProviderCapability:
        return ProviderCapability(
            "datacite-public-api-v2",
            "RESEARCH_DATASET_METADATA_API",
            True,
            self.directory.enabled,
            self.directory.health_status,
            False,
            self.directory.countries,
            self.directory.languages,
            self.directory.last_checked_at,
            self.directory.unavailable_reason,
            required_channel,
        )

    def search(self, request: SearchRequest, required_channel: bool) -> ProviderOutcome:
        capability = replace(
            self.capability(required_channel), last_checked_at=dt.datetime.now(dt.timezone.utc).isoformat()
        )
        query = request.query_text()
        params = urllib.parse.urlencode(
            {
                "query": query,
                "resource-type-id": "dataset",
                "page[size]": min(20, request.maximum_results),
                "disable-facets": "true",
                "sort": "-updated",
            }
        )
        domain = urllib.parse.urlsplit(self.directory.base_url).hostname or ""
        try:
            fetched = self.fetcher.fetch(f"{self.directory.base_url}?{params}", {"Accept": "application/vnd.api+json"})
            value = json.loads(fetched.payload)
            results = []
            for row in value.get("data", [])[: request.maximum_results]:
                attributes = row.get("attributes", {})
                titles = attributes.get("titles") or []
                descriptions = attributes.get("descriptions") or []
                doi = str(attributes.get("doi") or row.get("id") or "")
                if not doi:
                    continue
                canonical, _ = canonicalize("https://doi.org/" + urllib.parse.quote(doi, safe="/"))
                results.append(
                    SearchResult(
                        canonical,
                        str(titles[0].get("title") if titles else doi)[:500],
                        str(attributes.get("publisher") or "DataCite registrant")[:500],
                        str(descriptions[0].get("description") if descriptions else "")[:2000],
                        self.directory.source_tier,
                        None,
                        "METADATA_LICENSE_REVIEW_REQUIRED",
                    )
                )
            evidence = RequestEvidence(
                capability.provider_id,
                self.directory.directory_id,
                self.directory.name,
                request.country_iso3,
                fetched.domain,
                request.summary(),
                _sha(query),
                True,
                fetched.http_status,
                fetched.elapsed_ms,
                len(results),
                fetched.response_sha256,
                "SUCCESS" if results else "NO_RESULTS",
                None,
                required_channel,
            )
            return ProviderOutcome(capability, evidence, tuple(results))
        except (FetchBlocked, json.JSONDecodeError, TypeError, ValueError) as exc:
            code = exc.code if isinstance(exc, FetchBlocked) else "INVALID_PROVIDER_RESPONSE"
            attempted = exc.network_attempted if isinstance(exc, FetchBlocked) else True
            status = exc.http_status if isinstance(exc, FetchBlocked) else None
            return ProviderOutcome(
                capability,
                _failure_evidence(
                    capability,
                    request,
                    self.directory.directory_id,
                    self.directory.name,
                    domain,
                    code,
                    network_request=attempted,
                    http_status=status,
                ),
                (),
            )


def default_providers(registry: DirectoryRegistry) -> tuple[DiscoveryProvider, ...]:
    providers: list[DiscoveryProvider] = [BraveSearchProvider()]
    datacite = next((item for item in registry.directories if item.directory_id == "datacite-public-api-v2"), None)
    if datacite is not None:
        providers.append(DataCiteProvider(datacite))
    return tuple(providers)


def capability_snapshot(registry: DirectoryRegistry | None = None) -> dict[str, object]:
    selected = registry or DirectoryRegistry.load()
    capabilities = [provider.capability(False).evidence() for provider in default_providers(selected)]
    protocol_targets = {name: 0 for name in ("CKAN", "SOCRATA", "ODATA")}
    for directory in selected.directories:
        if directory.enabled and directory.protocol in protocol_targets:
            protocol_targets[directory.protocol] += 1
    for protocol, target_count in protocol_targets.items():
        capabilities.append(
            ProviderCapability(
                f"{protocol.casefold()}-adapter-v1",
                "PROTOCOL_ADAPTER",
                True,
                True,
                "READY" if target_count else "NO_REGISTERED_TARGET",
                False,
                ("*",),
                ("*",),
                dt.datetime.now(dt.timezone.utc).isoformat(),
                None if target_count else "NO_REGISTERED_DIRECTORY_FOR_PROTOCOL",
                False,
            ).evidence()
        )
    return {"registry_version": selected.version, "providers": capabilities}
