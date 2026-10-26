from __future__ import annotations

from dataclasses import asdict

from sqlalchemy.orm import Session

from crawler_agent.source_registry import SourceRegistryService
from crawler_optimizer_agent.connector_spec import infer_connector_spec_from_source
from source_discovery_agent.candidate_store import SourceCandidateStore
from source_discovery_agent.query_builder import build_discovery_queries
from source_discovery_agent.search_result_parser import load_search_results_fixture, parse_search_results_fixture
from source_discovery_agent.source_evaluator import evaluate_search_result, rank_source_candidates
from source_discovery_agent.types import DiscoveryReport, SearchResult, SourceCandidate


class SourceDiscoveryAgent:
    def __init__(self, session: Session):
        self.session = session

    def discover_from_fixture(
        self,
        *,
        country: str,
        disease: str,
        fixture_path: str,
        min_source_score: float = 0.35,
        generate_connector_specs: bool = True,
    ) -> DiscoveryReport:
        results = load_search_results_fixture(fixture_path)
        return self._discover_from_results(
            country=country,
            disease=disease,
            results=results,
            min_source_score=min_source_score,
            generate_connector_specs=generate_connector_specs,
        )

    def discover_from_search_results(
        self,
        *,
        country: str,
        disease: str,
        search_results: dict | list,
        min_source_score: float = 0.35,
        generate_connector_specs: bool = True,
    ) -> DiscoveryReport:
        results = parse_search_results_fixture(search_results)
        return self._discover_from_results(
            country=country,
            disease=disease,
            results=results,
            min_source_score=min_source_score,
            generate_connector_specs=generate_connector_specs,
        )

    def discover_from_provider_result(
        self,
        *,
        country: str,
        disease: str,
        provider_result,
        min_source_score: float = 0.35,
        generate_connector_specs: bool = True,
    ) -> DiscoveryReport:
        search_results = getattr(provider_result, "search_results", None)
        if search_results is None and isinstance(provider_result, dict):
            search_results = provider_result.get("search_results")
        report = self.discover_from_search_results(
            country=country,
            disease=disease,
            search_results=search_results or [],
            min_source_score=min_source_score,
            generate_connector_specs=generate_connector_specs,
        )
        provider_name = getattr(provider_result, "provider", None)
        network_accessed = getattr(provider_result, "network_accessed", False)
        provider_warnings = list(getattr(provider_result, "warnings", []) or [])
        return DiscoveryReport(
            status=report.status,
            country=report.country,
            disease=report.disease,
            queries=report.queries,
            search_results_seen=report.search_results_seen,
            candidates_found=report.candidates_found,
            candidates_inserted_or_updated=report.candidates_inserted_or_updated,
            skipped=report.skipped,
            connector_specs_generated=report.connector_specs_generated,
            top_candidates=report.top_candidates,
            warnings=[
                *provider_warnings,
                f"Provider result processed locally from {provider_name or 'unknown'}; network_accessed={bool(network_accessed)}.",
                "All inserted or updated sources remain pending_review and require human review.",
                "ConnectorSpec suggestions are drafts and do not create connector code.",
            ],
        )

    def _discover_from_results(
        self,
        *,
        country: str,
        disease: str,
        results: list[SearchResult],
        min_source_score: float,
        generate_connector_specs: bool,
    ) -> DiscoveryReport:
        queries = build_discovery_queries(country, disease)
        candidates = [
            candidate
            for result in results
            if (candidate := evaluate_search_result(result, country=country, disease=disease)) is not None
        ]
        ranked = rank_source_candidates(candidates)
        store_summary = SourceCandidateStore(self.session).save_candidates(
            ranked,
            min_source_score=min_source_score,
        )
        connector_specs = self._connector_specs(
            ranked,
            min_source_score=min_source_score,
            enabled=generate_connector_specs,
        )
        top_candidates = _candidate_summaries(ranked, connector_specs=connector_specs)
        warnings = [
            "Fixture-only discovery: no live search or external network request was executed.",
            "All inserted or updated sources remain pending_review and require human review.",
            "ConnectorSpec suggestions are drafts and do not create connector code.",
        ]
        return DiscoveryReport(
            status="ok",
            country=country,
            disease=disease,
            queries=[asdict(query) for query in queries],
            search_results_seen=len(results),
            candidates_found=len(ranked),
            candidates_inserted_or_updated=int(store_summary["inserted_or_updated"]),
            skipped=int(store_summary["skipped_low_score"]),
            connector_specs_generated=len(connector_specs),
            top_candidates=top_candidates,
            warnings=warnings,
        )

    def _connector_specs(
        self,
        candidates: list[SourceCandidate],
        *,
        min_source_score: float,
        enabled: bool,
    ) -> dict[str, dict]:
        if not enabled:
            return {}
        registry = SourceRegistryService(self.session)
        specs: dict[str, dict] = {}
        threshold = max(float(min_source_score), 0.55)
        for candidate in candidates:
            if candidate.source_score < threshold:
                continue
            source = registry.get_source_by_base_url(candidate.base_url)
            if source is None:
                continue
            spec = infer_connector_spec_from_source(source, sample_urls=[candidate.base_url])
            specs[candidate.base_url] = spec.to_dict()
        return specs


def _candidate_summaries(
    candidates: list[SourceCandidate],
    *,
    connector_specs: dict[str, dict],
    limit: int = 8,
) -> list[dict]:
    rows: list[dict] = []
    for candidate in candidates[:limit]:
        row = {
            "name": candidate.name,
            "base_url": candidate.base_url,
            "domain": candidate.domain,
            "country_scope": candidate.country_scope,
            "disease_scope": candidate.disease_scope,
            "source_type": candidate.source_type,
            "authority_score": candidate.authority_score,
            "relevance_score": candidate.relevance_score,
            "structured_data_score": candidate.structured_data_score,
            "freshness_score": candidate.freshness_score,
            "legal_or_robots_penalty": candidate.legal_or_robots_penalty,
            "source_score": candidate.source_score,
            "requires_human_review": True,
            "evidence": candidate.evidence,
            "status": "pending_review",
        }
        if candidate.base_url in connector_specs:
            row["connector_spec"] = connector_specs[candidate.base_url]
        rows.append(row)
    return rows
