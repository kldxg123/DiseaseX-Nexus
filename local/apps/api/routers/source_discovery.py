from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from apps.api.deps import get_db_session
from apps.api.schemas import ControlledLiveSourceDiscoveryRequest, SourceDiscoveryFixtureRequest
from apps.api.safety import sanitize_mapping
from source_discovery_agent import SourceDiscoveryAgent
from source_discovery_agent.live_guard import (
    DEFAULT_LIVE_SOURCE_DISCOVERY_POLICY,
    decide_live_discovery_access,
    load_live_discovery_policy,
)
from source_discovery_agent.live_providers import LiveDiscoveryRequest, get_live_discovery_provider
from source_discovery_agent.live_providers.types import LiveDiscoveryProviderResult

router = APIRouter(prefix="/source-discovery", tags=["Source Discovery"])


@router.post("/discover-fixture")
def discover_fixture(
    request: SourceDiscoveryFixtureRequest,
    session: Session = Depends(get_db_session),
) -> dict:
    report = SourceDiscoveryAgent(session).discover_from_search_results(
        country=request.country,
        disease=request.disease,
        search_results=request.search_results,
        min_source_score=request.min_source_score,
        generate_connector_specs=request.generate_connector_specs,
    )
    return _safe_discovery_payload(report)


@router.post("/discover-live-controlled")
def discover_live_controlled(
    request: ControlledLiveSourceDiscoveryRequest,
    session: Session = Depends(get_db_session),
) -> dict:
    provider = str(request.provider or "").strip().lower()
    provider_names = _controlled_provider_sequence(provider, max_requests=int(request.max_requests))
    policy = load_live_discovery_policy(DEFAULT_LIVE_SOURCE_DISCOVERY_POLICY)
    denied = _first_denied_provider(
        provider_names,
        policy=policy,
        allow_network=bool(request.allow_network),
        manual_reviewed=bool(request.manual_reviewed),
        terms_reviewed=bool(request.terms_reviewed),
    )
    if denied is not None:
        denied_provider, decision = denied
        return sanitize_mapping(
            {
                "status": "failed",
                "provider": provider,
                "providers_attempted": [],
                "denied_provider": denied_provider,
                "country": request.country,
                "disease": request.disease,
                "network_accessed": False,
                "request_count": 0,
                "candidate_urls_accessed": False,
                "follow_links": False,
                "error": decision.get("reason") or "live discovery denied by policy",
                "warnings": decision.get("warnings", []),
                "limitations": "Enable allow_network, terms_reviewed and manual_reviewed for controlled live discovery.",
            }
        )
    provider_result = _run_controlled_provider_sequence(
        provider_names,
        country=request.country,
        disease=request.disease,
    )
    report = SourceDiscoveryAgent(session).discover_from_provider_result(
        country=request.country,
        disease=request.disease,
        provider_result=provider_result,
        min_source_score=request.min_source_score,
        generate_connector_specs=request.generate_connector_specs,
    )
    payload = _safe_discovery_payload(report)
    live_status = (
        "failed"
        if provider_result.failure_type and report.candidates_found < 1
        else payload.get("status", "ok")
    )
    payload.update(
        {
            "status": live_status,
            "provider": provider,
            "true_provider_accessed": provider_result.provider,
            "providers_attempted": provider_result.metadata.get("providers_attempted", [provider_result.provider]),
            "network_accessed": bool(provider_result.network_accessed),
            "request_count": int(provider_result.request_count),
            "candidate_urls_accessed": False,
            "follow_links": False,
            "failure_type": provider_result.failure_type,
            "retry_allowed": bool(provider_result.retry_allowed),
            "cooldown_recommended": bool(provider_result.cooldown_recommended),
            "retry_after_seconds": provider_result.retry_after_seconds,
            "provider_response_summary": provider_result.metadata,
            "limitations": (
                "Controlled live source discovery only: no candidate URL visits, no follow links, "
                "all sources remain pending_review."
            ),
        }
    )
    return sanitize_mapping(payload)


def _controlled_provider_sequence(provider: str, *, max_requests: int) -> list[str]:
    normalized = str(provider or "").strip().lower()
    if normalized == "auto":
        return ["gdelt", "data_gov", "official_directory"][: max(1, min(int(max_requests), 3))]
    if normalized in {"gdelt", "data_gov", "official_directory"}:
        return [normalized]
    raise ValueError("Controlled live source discovery supports provider=auto, gdelt, data_gov, or official_directory.")


def _first_denied_provider(
    providers: list[str],
    *,
    policy: dict,
    allow_network: bool,
    manual_reviewed: bool,
    terms_reviewed: bool,
) -> tuple[str, dict] | None:
    for provider_name in providers:
        decision = decide_live_discovery_access(
            provider=provider_name,
            policy=policy,
            allow_network=allow_network,
            manual_reviewed=manual_reviewed,
            terms_reviewed=terms_reviewed,
            max_requests=1,
        )
        if not decision.get("allowed"):
            return provider_name, decision
    return None


def _run_controlled_provider_sequence(
    providers: list[str],
    *,
    country: str,
    disease: str,
) -> LiveDiscoveryProviderResult:
    results: list[LiveDiscoveryProviderResult] = []
    combined_results: list[dict] = []
    warnings: list[str] = []
    request_count = 0
    network_accessed = False
    for provider_name in providers:
        result = get_live_discovery_provider(provider_name).discover(
            LiveDiscoveryRequest(
                country=country,
                disease=disease,
                provider=provider_name,
                max_requests=1,
                allow_network=True,
                terms_reviewed=True,
                manual_reviewed=True,
                metadata=_provider_metadata(provider_name, country=country, disease=disease),
            )
        )
        results.append(result)
        request_count += int(result.request_count)
        network_accessed = network_accessed or bool(result.network_accessed)
        warnings.extend([f"{provider_name}: {warning}" for warning in result.warnings or []])
        combined_results.extend(result.search_results or [])
        if result.search_results:
            break
    return LiveDiscoveryProviderResult(
        provider="auto" if len(providers) > 1 else providers[0],
        request_count=request_count,
        search_results=combined_results,
        warnings=warnings,
        network_accessed=network_accessed,
        metadata={
            "providers_attempted": [item.provider for item in results],
            "provider_summaries": [
                {
                    "provider": item.provider,
                    "request_count": item.request_count,
                    "search_results_seen": len(item.search_results or []),
                    "network_accessed": item.network_accessed,
                    "failure_type": item.failure_type,
                    "cooldown_recommended": item.cooldown_recommended,
                    "status_code": item.metadata.get("status_code"),
                    "request_url_domain": item.metadata.get("request_url_domain"),
                }
                for item in results
            ],
        },
        failure_type=None if combined_results else _combined_failure_type(results),
        retry_allowed=False,
        cooldown_recommended=any(bool(item.cooldown_recommended) for item in results),
        retry_after_seconds=next((item.retry_after_seconds for item in results if item.retry_after_seconds), None),
    )


def _combined_failure_type(results: list[LiveDiscoveryProviderResult]) -> str | None:
    if not results:
        return "no_provider_attempted"
    if any(item.failure_type == "rate_limited" for item in results):
        return "rate_limited"
    return results[-1].failure_type or "no_results"


def _provider_metadata(provider: str, *, country: str, disease: str) -> dict:
    metadata: dict[str, object] = {"execute_live": True, "max_records": 10, "timeout_seconds": 15.0}
    if provider == "official_directory":
        metadata["directory_url"] = _official_directory_url(country=country, disease=disease)
    return metadata


def _official_directory_url(*, country: str, disease: str) -> str:
    text = f"{country} {disease}".strip().lower()
    if "influenza" in text or "flu" in text or "流感" in text:
        return "https://www.cdc.gov/fluview/index.html"
    if "norovirus" in text or "norostat" in text or "诺如" in text:
        return "https://www.cdc.gov/norovirus/php/reporting/norostat-data-table.html"
    return "https://www.cdc.gov/outbreaks/index.html"


def _safe_discovery_payload(report) -> dict:
    candidates = [_safe_candidate(candidate) for candidate in list(report.top_candidates or [])[:10]]
    connector_specs = _safe_connector_specs(report.top_candidates or [])
    return sanitize_mapping(
        {
            "status": report.status,
            "country": report.country,
            "disease": report.disease,
            "search_results_seen": report.search_results_seen,
            "candidates_found": report.candidates_found,
            "candidates_inserted_or_updated": report.candidates_inserted_or_updated,
            "skipped": report.skipped,
            "connector_specs_generated": report.connector_specs_generated,
            "top_candidates": candidates,
            "connector_specs": connector_specs,
            "warnings": list(report.warnings or []),
            "network_accessed": False,
            "limitations": (
                "Fixture/mock search results only; candidate sources remain pending_review and require "
                "human review before activation."
            ),
        }
    )


def _safe_candidate(candidate: dict[str, Any]) -> dict:
    return {
        "name": candidate.get("name"),
        "base_url": candidate.get("base_url"),
        "domain": candidate.get("domain"),
        "source_type": candidate.get("source_type"),
        "source_score": candidate.get("source_score"),
        "requires_human_review": True,
    }


def _safe_connector_specs(candidates: list[dict[str, Any]]) -> list[dict]:
    rows: list[dict] = []
    for candidate in candidates[:10]:
        spec = candidate.get("connector_spec")
        if not isinstance(spec, dict):
            continue
        rows.append(
            {
                "connector_name": spec.get("connector_name"),
                "source_name": spec.get("source_name") or candidate.get("name"),
                "source_type": spec.get("source_type") or candidate.get("source_type"),
                "parser_strategy": spec.get("parser_strategy"),
                "sample_url_count": spec.get("sample_url_count", len(spec.get("sample_urls") or [])),
                "requires_human_review": True,
                "test_fixture_required": bool(spec.get("test_fixture_required", True)),
            }
        )
    return rows
