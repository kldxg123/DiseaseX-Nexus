from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import time
import urllib.parse
from collections.abc import Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Protocol

from epidemic_core.discovery.safe_fetcher import FetchBlocked, SafeFetcher, canonicalize, public_addresses

REGISTRY_ENV = "DISCOVERY_DIRECTORY_REGISTRY"
BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
PROJECT_ROOT = Path(__file__).resolve().parents[3]
OPENCLAW_CLI = str(PROJECT_ROOT / "runtime/openclaw-npm/node_modules/.bin/openclaw")
OPENCLAW_ROOT = str(PROJECT_ROOT)
PROMPT_INJECTION = re.compile(
    r"(?:ignore|disregard|override)\s+(?:all\s+)?(?:previous|prior|system)|"
    r"(?:system|developer)\s+prompt|(?:call|invoke|use)\s+(?:the\s+)?(?:exec|shell|process)|"
    r"忽略.{0,12}(?:指令|提示)|执行.{0,8}(?:命令|工具)|系统提示",
    re.IGNORECASE,
)


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
                str(PROJECT_ROOT / "config/source_discovery/directories.v1.json"),
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

    def query_plan(self) -> tuple[str, str, str]:
        metrics = " ".join(self.desired_metrics)
        local_country = self.country_name_local or self.country_name_en
        local_disease = self.disease_name_local or self.disease_name_en
        year = dt.datetime.now(dt.timezone.utc).year
        if self.country_iso3 == "BRA":
            return (
                f"site:saude.gov.br Brasil dengue SINAN dados abertos API {year}",
                f"site:dadosabertos.saude.gov.br dengue casos notificações CSV JSON {year}",
                f"Brazil dengue surveillance cases incidence open data API {metrics} {year}",
            )
        if self.country_iso3 == "FRA":
            return (
                f"site:data.gouv.fr France COVID-19 indicateurs données ouvertes {year}",
                f"France COVID-19 cas décès incidence surveillance CSV API {year}",
                f"France COVID-19 weekly open data {metrics} {year - 1} {year}",
            )
        if self.country_iso3 == "UGA":
            return (
                f"site:evd-daily.health.go.ug Uganda Ministry Health Ebola daily confirmed cases API {year}",
                f"site:health.go.ug Uganda Ebola Bundibugyo daily dashboard confirmed cases {year}",
                "Uganda Ministry of Health EVD Daily official API confirmed cases date",
            )
        if self.country_iso3 == "COD":
            return (
                f"site:afro.who.int DRC Bundibugyo Ebola weekly external situation report PDF {year}",
                f"site:iris.who.int Bundibugyo virus disease Democratic Republic Congo weekly report {year}",
                "WHO AFRO DRC Ebola Bundibugyo weekly situation report confirmed cases deaths PDF",
            )
        if self.country_iso3 == "GBR":
            return (
                f"site:gov.uk UKHSA national influenza surveillance report data ODS week {year}",
                f"site:assets.publishing.service.gov.uk influenza laboratory confirmed ECMO admissions ODS {year}",
                "UKHSA weekly influenza report laboratory confirmed hospital admissions data download",
            )
        return (
            f"site:gov.cn OR site:nhc.gov.cn {self.country_name_en} {self.country_iso3} "
            f"{self.disease_name_en} SARS-CoV-2 weekly surveillance CSV API {year}",
            f"{local_country} {local_disease} 新冠 病例 死亡 发病率 每周 周报 月报 开放数据 {year - 1} {year}",
            f"{self.country_name_en} {self.disease_name_en} cases deaths incidence positivity "
            f"weekly monthly dataset CSV XLSX JSON OData {metrics}",
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
    provider_id: str = "unknown-provider"
    query_id: str = "query-1"
    rank: int = 1
    original_url: str | None = None
    landing_page_url: str | None = None
    stable_dataset_id: str | None = None
    observed_at: str | None = None
    result_sha256: str | None = None
    metadata: dict[str, object] | None = None


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
    query_count: int = 1
    successful_query_count: int = 0
    prompt_injection_blocked: int = 0

    def event_details(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class ProviderOutcome:
    capability: ProviderCapability
    evidence: RequestEvidence
    results: tuple[SearchResult, ...]
    blocked_result_count: int = 0


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
    def __init__(
        self,
        key: str | None = None,
        fetcher: SafeFetcher | None = None,
        *,
        enabled: bool | None = None,
    ):
        self.enabled = enabled if enabled is not None else os.environ.get("DISCOVERY_BRAVE_ENABLED", "0") == "1"
        self.key = (key if key is not None else os.environ.get("BRAVE_SEARCH_API_KEY", "")) if self.enabled else ""
        self.fetcher = fetcher or SafeFetcher(maximum_bytes=4 * 1024 * 1024, timeout_seconds=15)

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


def _openclaw_environment() -> dict[str, str]:
    path = ":".join(
        (
            f"{OPENCLAW_ROOT}/runtime/node-current/bin",
            f"{OPENCLAW_ROOT}/runtime/openclaw-npm/node_modules/.bin",
            "/usr/local/sbin",
            "/usr/local/bin",
            "/usr/sbin",
            "/usr/bin",
            "/sbin",
            "/bin",
        )
    )
    environment = {
        "PATH": path,
        "HOME": str(Path.home()),
        "LANG": "C.UTF-8",
        "NODE_HOME": f"{OPENCLAW_ROOT}/runtime/node-current",
        "OPENCLAW_HOME": f"{OPENCLAW_ROOT}/runtime/openclaw-npm",
        "OPENCLAW_STATE_DIR": f"{OPENCLAW_ROOT}/storage/openclaw/state",
        "OPENCLAW_CONFIG_PATH": f"{OPENCLAW_ROOT}/storage/openclaw/state/openclaw.json",
        "OPENCLAW_WORKSPACE": f"{OPENCLAW_ROOT}/storage/openclaw/workspace",
    }
    return environment


def _result_rows(value: object) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def visit(item: object) -> None:
        if isinstance(item, dict):
            url = item.get("url") or item.get("link") or item.get("href")
            if isinstance(url, str) and url.startswith("https://"):
                rows.append(item)
            for child in item.values():
                if isinstance(child, (dict, list)):
                    visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return rows


def _safe_search_url(url: str) -> str:
    canonical, parsed = canonicalize(url)
    try:
        public_addresses(parsed.hostname or "")
    except OSError as exc:
        raise FetchBlocked("DNS_ERROR") from exc
    return canonical


def _result(
    *,
    url: str,
    title: str,
    publisher: str,
    description: str,
    source_tier: str,
    provider_id: str,
    query_id: str,
    rank: int,
    media_type: str | None = None,
    license_status: str = "UNKNOWN_REVIEW_REQUIRED",
    landing_page_url: str | None = None,
    stable_dataset_id: str | None = None,
    metadata: dict[str, object] | None = None,
    validator: Any = _safe_search_url,
) -> SearchResult:
    canonical = validator(url)
    observed = dt.datetime.now(dt.timezone.utc).isoformat()
    evidence = {
        "provider_id": provider_id,
        "query_id": query_id,
        "rank": rank,
        "title": title[:500],
        "publisher": publisher[:500],
        "canonical_url": canonical,
        "stable_dataset_id": stable_dataset_id,
        "observed_at": observed,
    }
    return SearchResult(
        canonical,
        title[:500],
        publisher[:500],
        description[:2000],
        source_tier,
        media_type,
        license_status,
        provider_id,
        query_id,
        rank,
        url,
        landing_page_url,
        stable_dataset_id,
        observed,
        _sha(json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":"))),
        metadata or {},
    )


class OpenClawDuckDuckGoProvider:
    """Key-free OpenClaw provider. Search output remains untrusted metadata."""

    def __init__(self, runner: Any | None = None, url_validator: Any | None = None):
        self.enabled = os.environ.get("DISCOVERY_DUCKDUCKGO_ENABLED", "1") == "1"
        self.runner = runner or self._run
        self.url_validator = url_validator or _safe_search_url
        self.provider_label = "OpenClaw DuckDuckGo"
        self.provider_domain = "duckduckgo.com"
        self.query_prefix = "ddg"

    def capability(self, required_channel: bool) -> ProviderCapability:
        return ProviderCapability(
            "openclaw-duckduckgo-v1",
            "KEY_FREE_EXPERIMENTAL_WEB_SEARCH",
            True,
            self.enabled,
            "READY" if self.enabled else "DISABLED",
            False,
            ("*",),
            ("*",),
            None,
            None if self.enabled else "PROVIDER_DISABLED",
            required_channel,
        )

    @staticmethod
    def _run(query: str, limit: int) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                OPENCLAW_CLI,
                "capability",
                "web",
                "search",
                "--provider",
                "duckduckgo",
                "--query",
                query,
                "--limit",
                str(limit),
                "--json",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            env=_openclaw_environment(),
        )

    def search(self, request: SearchRequest, required_channel: bool) -> ProviderOutcome:
        capability = replace(
            self.capability(required_channel), last_checked_at=dt.datetime.now(dt.timezone.utc).isoformat()
        )
        queries = request.query_plan()
        if not capability.enabled:
            return ProviderOutcome(
                capability,
                RequestEvidence(
                    capability.provider_id,
                    None,
                    self.provider_label,
                    request.country_iso3,
                    self.provider_domain,
                    request.summary(),
                    _sha("\n".join(queries)),
                    False,
                    None,
                    None,
                    0,
                    None,
                    "UNAVAILABLE",
                    "PROVIDER_DISABLED",
                    required_channel,
                    len(queries),
                    0,
                    0,
                ),
                (),
            )
        started = time.monotonic()
        results: dict[str, SearchResult] = {}
        successful_queries = 0
        response_hashes: list[str] = []
        injection_blocked = 0
        unsafe_blocked = 0
        failure_codes: list[str] = []
        for query_number, query in enumerate(queries, 1):
            try:
                completed = self.runner(query, min(10, request.maximum_results))
            except subprocess.TimeoutExpired:
                failure_codes.append("REQUEST_TIMEOUT")
                continue
            if completed.returncode != 0:
                diagnostic = (completed.stderr + completed.stdout).casefold()
                if any(word in diagnostic for word in ("captcha", "challenge", "rate limit", "429")):
                    failure_codes.append("PROVIDER_DEGRADED")
                else:
                    failure_codes.append("PROVIDER_UNAVAILABLE")
                continue
            try:
                payload = json.loads(completed.stdout)
            except json.JSONDecodeError:
                failure_codes.append("INVALID_PROVIDER_RESPONSE")
                continue
            successful_queries += 1
            response_hashes.append(_sha(completed.stdout))
            for rank, row in enumerate(_result_rows(payload), 1):
                raw_url = str(row.get("url") or row.get("link") or row.get("href") or "")
                try:
                    safe_url = self.url_validator(raw_url)
                except (FetchBlocked, OSError):
                    unsafe_blocked += 1
                    continue
                title = str(row.get("title") or row.get("name") or "Untitled result")[:500]
                description = str(
                    row.get("snippet") or row.get("description") or row.get("text") or row.get("content") or ""
                )[:2000]
                if PROMPT_INJECTION.search(title + "\n" + description):
                    injection_blocked += 1
                    continue
                results.setdefault(
                    safe_url,
                    _result(
                        url=safe_url,
                        title=title,
                        publisher=urllib.parse.urlsplit(safe_url).hostname or "unknown publisher",
                        description=description,
                        source_tier="GENERAL_WEB_SEARCH",
                        provider_id=capability.provider_id,
                        query_id=f"{self.query_prefix}-{query_number}",
                        rank=rank,
                        validator=lambda value: value,
                    ),
                )
        elapsed_ms = int((time.monotonic() - started) * 1000)
        if successful_queries == len(queries):
            result_code, reason = ("SUCCESS" if results else "NO_RESULTS"), None
        elif successful_queries:
            result_code, reason = "DEGRADED", "PROVIDER_DEGRADED"
        else:
            result_code, reason = "FAILED", (failure_codes[-1] if failure_codes else "PROVIDER_UNAVAILABLE")
        evidence = RequestEvidence(
            capability.provider_id,
            None,
            self.provider_label,
            request.country_iso3,
            self.provider_domain,
            request.summary(),
            _sha("\n".join(queries)),
            True,
            None,
            elapsed_ms,
            len(results),
            _sha("".join(response_hashes)) if response_hashes else None,
            result_code,
            reason,
            required_channel,
            len(queries),
            successful_queries,
            injection_blocked,
        )
        return ProviderOutcome(capability, evidence, tuple(results.values()), injection_blocked + unsafe_blocked)


class OpenClawParallelFreeProvider(OpenClawDuckDuckGoProvider):
    """Explicit fixed-version Parallel Free attempt; absence degrades without blocking."""

    def __init__(self, runner: Any | None = None, url_validator: Any | None = None):
        super().__init__(runner=runner, url_validator=url_validator)
        self.enabled = os.environ.get("DISCOVERY_PARALLEL_FREE_ENABLED", "1") == "1"
        self.provider_label = "OpenClaw Parallel Search Free"
        self.provider_domain = "parallel.ai"
        self.query_prefix = "parallel"

    def capability(self, required_channel: bool) -> ProviderCapability:
        return ProviderCapability(
            "openclaw-parallel-free-v1",
            "KEY_FREE_OPTIONAL_WEB_SEARCH",
            True,
            self.enabled,
            "PROBE_REQUIRED" if self.enabled else "DISABLED",
            False,
            ("*",),
            ("*",),
            None,
            None if self.enabled else "PROVIDER_DISABLED",
            required_channel,
        )

    def search(self, request: SearchRequest, required_channel: bool) -> ProviderOutcome:
        outcome = super().search(request, required_channel)
        if outcome.evidence.result == "FAILED" and outcome.evidence.reason_code == "PROVIDER_UNAVAILABLE":
            return ProviderOutcome(
                outcome.capability,
                replace(outcome.evidence, reason_code="PROVIDER_DEGRADED"),
                outcome.results,
                outcome.blocked_result_count,
            )
        return outcome

    @staticmethod
    def _run(query: str, limit: int) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                OPENCLAW_CLI,
                "capability",
                "web",
                "search",
                "--provider",
                "parallel-free",
                "--query",
                query,
                "--limit",
                str(limit),
                "--json",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            env=_openclaw_environment(),
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
            for rank, row in enumerate(value.get("data", [])[: request.maximum_results], 1):
                attributes = row.get("attributes", {})
                titles = attributes.get("titles") or []
                descriptions = attributes.get("descriptions") or []
                doi = str(attributes.get("doi") or row.get("id") or "")
                if not doi:
                    continue
                canonical, _ = canonicalize("https://doi.org/" + urllib.parse.quote(doi, safe="/"))
                results.append(
                    _result(
                        url=canonical,
                        title=str(titles[0].get("title") if titles else doi),
                        publisher=str(attributes.get("publisher") or "DataCite registrant"),
                        description=str(descriptions[0].get("description") if descriptions else ""),
                        source_tier=self.directory.source_tier,
                        provider_id=capability.provider_id,
                        query_id="datacite-1",
                        rank=rank,
                        license_status="METADATA_LICENSE_REVIEW_REQUIRED",
                        stable_dataset_id=doi,
                        validator=lambda value: value,
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


class DataGouvFranceProvider:
    """Deterministic connector for the official French government dataset catalog."""

    FORMATS = {
        "csv": "text/csv",
        "tsv": "text/tab-separated-values",
        "json": "application/json",
        "ndjson": "application/x-ndjson",
        "jsonl": "application/x-ndjson",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "zip": "application/zip",
    }

    def __init__(self, directory: DirectoryDefinition, fetcher: SafeFetcher | None = None):
        self.directory = directory
        self.fetcher = fetcher or SafeFetcher(maximum_bytes=8 * 1024 * 1024, timeout_seconds=20)

    def capability(self, required_channel: bool) -> ProviderCapability:
        return ProviderCapability(
            "data-gouv-fr-datasets-api-v1",
            "GOVERNMENT_OPEN_DATA_CATALOG_API",
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

    @staticmethod
    def _queries(request: SearchRequest) -> tuple[str, str, str]:
        del request
        return (
            "covid indicateurs",
            "covid épidémie",
            "covid cas décès",
        )

    def search(self, request: SearchRequest, required_channel: bool) -> ProviderOutcome:
        capability = replace(
            self.capability(required_channel), last_checked_at=dt.datetime.now(dt.timezone.utc).isoformat()
        )
        queries = self._queries(request)
        domain = urllib.parse.urlsplit(self.directory.base_url).hostname or ""
        if not capability.enabled:
            return ProviderOutcome(
                capability,
                _failure_evidence(
                    capability,
                    request,
                    self.directory.directory_id,
                    self.directory.name,
                    domain,
                    capability.unavailable_reason or "PROVIDER_DISABLED",
                    network_request=False,
                ),
                (),
            )
        started = time.monotonic()
        results: dict[str, SearchResult] = {}
        response_hashes: list[str] = []
        successful = 0
        last_status: int | None = None
        failures: list[str] = []
        blocked = 0
        for query_number, query in enumerate(queries, 1):
            params = urllib.parse.urlencode({"q": query, "page_size": 20, "sort": "-last_update"})
            try:
                fetched = self.fetcher.fetch(f"{self.directory.base_url}?{params}", {"Accept": "application/json"})
                last_status = fetched.http_status
                payload = json.loads(fetched.payload)
                datasets = payload.get("data", []) if isinstance(payload, dict) else []
                if not isinstance(datasets, list):
                    raise ValueError("INVALID_DATASET_LIST")
            except (FetchBlocked, json.JSONDecodeError, TypeError, ValueError) as exc:
                failures.append(exc.code if isinstance(exc, FetchBlocked) else "INVALID_PROVIDER_RESPONSE")
                if isinstance(exc, FetchBlocked):
                    last_status = exc.http_status
                continue
            successful += 1
            response_hashes.append(fetched.response_sha256)
            for dataset_rank, dataset in enumerate(datasets, 1):
                if not isinstance(dataset, dict):
                    continue
                dataset_id = str(dataset.get("id") or dataset.get("slug") or "")
                title = str(dataset.get("title") or "Untitled data.gouv.fr dataset")
                description = str(dataset.get("description") or dataset.get("description_short") or "")
                organization = dataset.get("organization")
                publisher = (
                    str(organization.get("name"))
                    if isinstance(organization, dict) and organization.get("name")
                    else "data.gouv.fr publisher"
                )
                landing_page = str(dataset.get("page") or "") or None
                license_value = dataset.get("license")
                license_id = (
                    str(license_value.get("id") or license_value.get("title") or "UNKNOWN")
                    if isinstance(license_value, dict)
                    else str(license_value or "UNKNOWN")
                )
                resources = dataset.get("resources")
                if not isinstance(resources, list):
                    continue
                dataset_evidence = "\n".join((title, description))
                if not re.search(
                    r"épid[ée]mi|epidemi|surveillance|indicateur|incidence|hospital|cas\b|décès|deces|death",
                    dataset_evidence,
                    re.I,
                ):
                    continue
                ordered_resources = sorted(
                    (item for item in resources if isinstance(item, dict)),
                    key=lambda item: str(item.get("last_modified") or item.get("created_at") or ""),
                    reverse=True,
                )[:5]
                for resource_rank, resource in enumerate(ordered_resources, 1):
                    if len(results) >= min(100, max(20, request.maximum_results * 4)):
                        break
                    raw_url = str(resource.get("url") or resource.get("latest") or "")
                    fmt = str(resource.get("format") or "").casefold().lstrip(".")
                    mime = str(resource.get("mime") or "").split(";", 1)[0].casefold()
                    media_type = self.FORMATS.get(fmt) or (mime if mime in self.FORMATS.values() else None)
                    if not raw_url.startswith("https://") or media_type is None:
                        continue
                    text_evidence = "\n".join(
                        (title, description, str(resource.get("title") or ""), str(resource.get("description") or ""))
                    )
                    if not re.search(r"covid|sars[- ]?cov|coronavirus", text_evidence, re.I):
                        continue
                    try:
                        item = _result(
                            url=raw_url,
                            title=f"{title} — {str(resource.get('title') or fmt.upper())}",
                            publisher=publisher,
                            description=text_evidence,
                            source_tier=self.directory.source_tier,
                            provider_id=capability.provider_id,
                            query_id=f"data-gouv-fr-{query_number}",
                            rank=(dataset_rank - 1) * 20 + resource_rank,
                            media_type=media_type,
                            license_status=f"{license_id}_REVIEW_REQUIRED",
                            landing_page_url=landing_page,
                            stable_dataset_id=dataset_id or None,
                            metadata={
                                "resource_id": str(resource.get("id") or ""),
                                "resource_format": fmt,
                                "resource_last_modified": str(resource.get("last_modified") or ""),
                                "dataset_last_update": str(dataset.get("last_update") or ""),
                                "catalog_domain": domain,
                            },
                        )
                    except (FetchBlocked, OSError):
                        blocked += 1
                        continue
                    results.setdefault(item.canonical_url, item)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        if successful == len(queries):
            result_code, reason = ("SUCCESS" if results else "NO_RESULTS"), None
        elif successful:
            result_code, reason = "DEGRADED", "PROVIDER_DEGRADED"
        else:
            result_code, reason = "FAILED", failures[-1] if failures else "PROVIDER_UNAVAILABLE"
        evidence = RequestEvidence(
            capability.provider_id,
            self.directory.directory_id,
            self.directory.name,
            request.country_iso3,
            domain,
            request.summary(),
            _sha("\n".join(queries)),
            True,
            last_status,
            elapsed_ms,
            len(results),
            _sha("".join(response_hashes)) if response_hashes else None,
            result_code,
            reason,
            required_channel,
            len(queries),
            successful,
            0,
        )
        return ProviderOutcome(capability, evidence, tuple(results.values()), blocked)


class BrazilOpenDataSUSDengueProvider:
    """Discover the current dengue API endpoint from the official OpenAPI document."""

    def __init__(self, directory: DirectoryDefinition, fetcher: SafeFetcher | None = None):
        self.directory = directory
        self.fetcher = fetcher or SafeFetcher(maximum_bytes=8 * 1024 * 1024, timeout_seconds=30)

    def capability(self, required_channel: bool) -> ProviderCapability:
        return ProviderCapability(
            "brazil-open-data-sus-dengue-v1",
            "NATIONAL_HEALTH_OPENAPI",
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
        domain = urllib.parse.urlsplit(self.directory.base_url).hostname or ""
        if not capability.enabled:
            return ProviderOutcome(
                capability,
                _failure_evidence(
                    capability,
                    request,
                    self.directory.directory_id,
                    self.directory.name,
                    domain,
                    capability.unavailable_reason or "PROVIDER_DISABLED",
                    network_request=False,
                ),
                (),
            )
        started = time.monotonic()
        try:
            fetched = self.fetcher.fetch(self.directory.base_url, {"Accept": "application/json"})
            specification = json.loads(fetched.payload)
            paths = specification.get("paths", {}) if isinstance(specification, dict) else {}
            endpoint_path = "/arboviroses/dengue"
            operation = paths.get(endpoint_path) if isinstance(paths, dict) else None
            if not isinstance(operation, dict) or not isinstance(operation.get("get"), dict):
                raise ValueError("DENGUE_ENDPOINT_NOT_DECLARED")
            origin = urllib.parse.urlunsplit((*urllib.parse.urlsplit(self.directory.base_url)[:2], "", "", ""))
            year = dt.datetime.now(dt.timezone.utc).year
            endpoint = urllib.parse.urljoin(origin + "/", endpoint_path.lstrip("/"))
            endpoint = f"{endpoint}?{urllib.parse.urlencode({'nu_ano': year, 'limit': 20, 'offset': 0})}"
            item = _result(
                url=endpoint,
                title=f"SINAN dengue case notifications — {year} official API sample",
                publisher=self.directory.publisher,
                description=(
                    "Official Brazil Ministry of Health dengue notification records discovered from the "
                    "versioned OpenAPI document; each API row represents one disease notification."
                ),
                source_tier=self.directory.source_tier,
                provider_id=capability.provider_id,
                query_id="open-data-sus-openapi-dengue-1",
                rank=1,
                media_type="application/json",
                license_status="CC_ATTRIBUTION_REVIEW_REQUIRED",
                stable_dataset_id="sinan-arboviroses-dengue",
                metadata={
                    "catalog_domain": domain,
                    "openapi_version": str(specification.get("swagger") or ""),
                    "api_version": str(specification.get("info", {}).get("version") or ""),
                    "openapi_response_sha256": fetched.response_sha256,
                    "record_semantics": "case_notification",
                    "derived_preview_field": "case_notification_count",
                    "requested_year": year,
                },
            )
        except (FetchBlocked, json.JSONDecodeError, TypeError, ValueError, OSError) as exc:
            code = exc.code if isinstance(exc, FetchBlocked) else "INVALID_PROVIDER_RESPONSE"
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
                    network_request=True,
                    http_status=status,
                ),
                (),
            )
        elapsed_ms = int((time.monotonic() - started) * 1000)
        evidence = RequestEvidence(
            capability.provider_id,
            self.directory.directory_id,
            self.directory.name,
            request.country_iso3,
            domain,
            request.summary(),
            _sha(f"{endpoint_path}:{year}:limit=20:offset=0"),
            True,
            fetched.http_status,
            elapsed_ms,
            1,
            fetched.response_sha256,
            "SUCCESS",
            None,
            required_channel,
            1,
            1,
            0,
        )
        return ProviderOutcome(capability, evidence, (item,))


def default_providers(registry: DirectoryRegistry) -> tuple[DiscoveryProvider, ...]:
    providers: list[DiscoveryProvider] = []
    data_gouv = next(
        (item for item in registry.directories if item.directory_id == "data-gouv-fr-datasets-api-v1"), None
    )
    if data_gouv is not None:
        providers.append(DataGouvFranceProvider(data_gouv))
    brazil_dengue = next(
        (item for item in registry.directories if item.directory_id == "brazil-open-data-sus-swagger-v1"), None
    )
    if brazil_dengue is not None:
        providers.append(BrazilOpenDataSUSDengueProvider(brazil_dengue))
    providers.append(OpenClawParallelFreeProvider())
    providers.append(OpenClawDuckDuckGoProvider())
    datacite = next((item for item in registry.directories if item.directory_id == "datacite-public-api-v2"), None)
    if datacite is not None:
        providers.append(DataCiteProvider(datacite))
    if os.environ.get("DISCOVERY_BRAVE_ENABLED", "0") == "1":
        providers.append(BraveSearchProvider(enabled=True))
    return tuple(providers)


def capability_snapshot(registry: DirectoryRegistry | None = None) -> dict[str, object]:
    selected = registry or DirectoryRegistry.load()
    capabilities = [provider.capability(False).evidence() for provider in default_providers(selected)]
    if not any(item["provider_id"] == "brave-web-search-v1" for item in capabilities):
        capabilities.append(BraveSearchProvider(enabled=False).capability(False).evidence())
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
