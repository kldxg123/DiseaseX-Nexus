from __future__ import annotations

from urllib.parse import urlsplit

from source_discovery_agent.live_guard import (
    DEFAULT_LIVE_SOURCE_DISCOVERY_POLICY,
    decide_live_discovery_access,
    load_live_discovery_policy,
)
from source_discovery_agent.live_providers import LiveDiscoveryRequest, get_live_discovery_provider
from source_discovery_agent.live_providers.types import LiveDiscoveryProviderResult

from live_source_search_orchestrator.provider_planner import curated_official_directory_url
from live_source_search_orchestrator.types import (
    DiseaseAliasSet,
    LiveSourceSearchRequest,
    ProviderPlan,
    ProviderSearchResult,
)


def run_provider_plan(
    plan: ProviderPlan,
    request: LiveSourceSearchRequest,
    aliases: DiseaseAliasSet,
    *,
    policy_path: str = DEFAULT_LIVE_SOURCE_DISCOVERY_POLICY,
) -> list[ProviderSearchResult]:
    if request.dry_run or not request.allow_network:
        return [
            ProviderSearchResult(
                provider=provider,
                request_count=0,
                search_results=[],
                network_accessed=False,
                status="planned",
                warnings=["Dry-run provider plan only; no network request executed."],
                metadata_summary={"query": _query_text(request, aliases), "dry_run": True},
            )
            for provider in plan.providers_planned
        ]
    policy = load_live_discovery_policy(policy_path)
    results: list[ProviderSearchResult] = []
    request_count_total = 0
    for provider in plan.providers_planned:
        if request_count_total >= int(plan.max_total_requests):
            break
        decision = decide_live_discovery_access(
            provider=provider,
            policy=policy,
            allow_network=request.allow_network,
            manual_reviewed=request.manual_reviewed,
            terms_reviewed=request.terms_reviewed,
            max_requests=1,
        )
        if not decision.get("allowed"):
            results.append(
                ProviderSearchResult(
                    provider=provider,
                    request_count=0,
                    search_results=[],
                    network_accessed=False,
                    status="failed",
                    failure_type="policy_denied",
                    retry_allowed=False,
                    safe_failure=True,
                    sanitized_error_summary=str(decision.get("reason") or "live discovery denied")[:240],
                    warnings=[str(decision.get("reason") or "live discovery denied")],
                    metadata_summary={"policy_allowed": False},
                )
            )
            continue
        provider_result = _call_provider(provider, request, aliases)
        request_count_total += int(provider_result.request_count)
        results.append(_provider_result_to_search_result(provider_result))
    return results


def _call_provider(
    provider: str,
    request: LiveSourceSearchRequest,
    aliases: DiseaseAliasSet,
) -> LiveDiscoveryProviderResult:
    metadata = {
        "execute_live": True,
        "max_records": 10,
        "timeout_seconds": 15.0,
    }
    if provider == "official_directory":
        url = _official_directory_url(request, aliases)
        if url:
            metadata["directory_url"] = url
    try:
        return get_live_discovery_provider(provider).discover(
            LiveDiscoveryRequest(
                country=request.country,
                disease=request.disease,
                provider=provider,
                query_text=_query_text(request, aliases),
                max_requests=1,
                allow_network=True,
                terms_reviewed=True,
                manual_reviewed=True,
                metadata=metadata,
            )
        )
    except Exception as exc:
        return LiveDiscoveryProviderResult(
            provider=provider,
            request_count=0,
            search_results=[],
            warnings=[f"Provider failed safely: {type(exc).__name__}."],
            network_accessed=False,
            metadata={},
            failure_type="provider_error",
            retry_allowed=False,
            cooldown_recommended=False,
        )


def _provider_result_to_search_result(result: LiveDiscoveryProviderResult) -> ProviderSearchResult:
    metadata = {
        "status_code": result.metadata.get("status_code"),
        "request_url_domain": result.metadata.get("request_url_domain"),
        "content_type": result.metadata.get("content_type"),
        "content_length": result.metadata.get("content_length"),
        "content_hash_prefix": result.metadata.get("content_hash_prefix"),
        "cooldown_recommended": result.cooldown_recommended,
        "retry_allowed": result.retry_allowed,
    }
    status_code = result.metadata.get("status_code")
    warnings = list(result.warnings or [])
    failure_type = _normalize_failure_type(result.failure_type)
    return ProviderSearchResult(
        provider=result.provider,
        request_count=int(result.request_count),
        search_results=[dict(row) for row in (result.search_results or []) if isinstance(row, dict)],
        warnings=warnings,
        network_accessed=bool(result.network_accessed),
        status=_status_for_provider_result(result, failure_type=failure_type),
        metadata_summary={key: value for key, value in metadata.items() if value is not None},
        failure_type=failure_type,
        http_status_code=int(status_code) if status_code is not None else None,
        retry_allowed=bool(result.retry_allowed),
        safe_failure=True,
        sanitized_error_summary=_safe_error_summary(warnings, failure_type=failure_type),
        candidate_urls_visited=False,
        follow_links=False,
    )


def _query_text(request: LiveSourceSearchRequest, aliases: DiseaseAliasSet) -> str:
    alias = aliases.aliases[0] if aliases.aliases else request.disease
    return f"{request.country} {alias} surveillance dataset"


def _official_directory_url(request: LiveSourceSearchRequest, aliases: DiseaseAliasSet) -> str | None:
    explicit = str(request.curated_official_directory_url or "").strip()
    if explicit:
        return explicit
    return curated_official_directory_url(request.disease, aliases)


def provider_domain(provider_result: ProviderSearchResult) -> str | None:
    domain = provider_result.metadata_summary.get("request_url_domain")
    return str(domain) if domain else None


def is_provider_endpoint_only(url: str) -> bool:
    host = urlsplit(url).hostname or ""
    return host in {"catalog.data.gov", "api.gdeltproject.org", "www.cdc.gov", "cdc.gov"}


def _normalize_failure_type(value: str | None) -> str | None:
    mapping = {
        "access_denied": "policy_denied",
        "missing_directory_url": "invalid_provider_config",
        "invalid_response": "parse_error",
        "network_error": "http_error",
    }
    if value is None:
        return None
    text = str(value).strip().lower()
    return mapping.get(text, text)


def _status_for_provider_result(result: LiveDiscoveryProviderResult, *, failure_type: str | None) -> str:
    if failure_type is None and result.search_results:
        return "ok"
    if failure_type == "no_results":
        return "no_results"
    if failure_type:
        return "failed"
    return "ok"


def _safe_error_summary(warnings: list[str], *, failure_type: str | None) -> str | None:
    if not warnings and not failure_type:
        return None
    text = " ".join(str(item) for item in warnings[:3]) or str(failure_type or "")
    text = " ".join(text.split())
    return text[:240] if text else None


__all__ = ["is_provider_endpoint_only", "provider_domain", "run_provider_plan"]
