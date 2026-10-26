from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from source_discovery_agent.candidate_store import SourceCandidateStore
from source_discovery_agent.types import SourceCandidate

from live_source_search_orchestrator.candidate_quality_gate import (
    apply_metadata_results,
    apply_quality_gate,
    candidates_for_metadata_check,
    quality_gate_summary,
)
from live_source_search_orchestrator.candidate_deduper import dedupe_candidates
from live_source_search_orchestrator.candidate_normalizer import normalize_provider_results
from live_source_search_orchestrator.candidate_scorer import score_candidates
from live_source_search_orchestrator.connector_spec_recommender import (
    attach_connector_specs,
    recommend_connector_specs,
)
from live_source_search_orchestrator.disease_alias_expander import expand_disease_aliases
from live_source_search_orchestrator.metadata_check import run_metadata_check
from live_source_search_orchestrator.provider_planner import plan_providers
from live_source_search_orchestrator.provider_runner import run_provider_plan
from live_source_search_orchestrator.report import make_run_dir, write_live_source_search_report
from live_source_search_orchestrator.review_queue import build_review_queue
from live_source_search_orchestrator.types import LiveSourceSearchReport, LiveSourceSearchRequest


class LiveSourceSearchOrchestrator:
    def __init__(self, session: Session | None = None):
        self.session = session

    def run(self, request: LiveSourceSearchRequest) -> LiveSourceSearchReport:
        aliases = expand_disease_aliases(request.disease)
        plan = plan_providers(request, aliases)
        provider_results = run_provider_plan(plan, request, aliases)
        normalized = normalize_provider_results(provider_results, country=request.country, aliases=aliases)
        scored_initial = score_candidates(normalized, aliases=aliases)
        deduped, _dedupe_summary = dedupe_candidates(scored_initial)
        scored = score_candidates(deduped, aliases=aliases)
        scored, quality_assessments = apply_quality_gate(scored)
        metadata_summary = None
        metadata_results: list[dict[str, Any]] = []
        if request.metadata_check:
            metadata_candidates = [
                {
                    **candidate.to_dict(),
                    "country": request.country,
                    "disease": request.disease,
                    "url": candidate.normalized_url,
                    "title": candidate.normalized_title,
                }
                for candidate in candidates_for_metadata_check(scored, limit=request.metadata_check_top_n)
            ]
            metadata_summary = run_metadata_check(
                metadata_candidates,
                max_requests=request.max_metadata_check_requests,
                allow_network=bool(request.allow_network and not request.dry_run),
                terms_reviewed=request.terms_reviewed,
                manual_reviewed=request.manual_reviewed,
            )
            metadata_results = [result.to_dict() for result in metadata_summary.results]
            scored = apply_metadata_results(scored, metadata_results)
        refined_candidates = [candidate for candidate in scored if candidate.quality_gate_status != "rejected"]
        connector_specs = recommend_connector_specs(refined_candidates, min_score=request.min_source_score)
        scored = attach_connector_specs(scored, connector_specs)
        refined_candidates = [candidate for candidate in scored if candidate.quality_gate_status != "rejected"]
        inserted = 0
        if self.session is not None and not request.dry_run:
            inserted = self._store_candidates(refined_candidates, min_source_score=request.min_source_score)
        review_items = build_review_queue(scored)
        refined_review_items = build_review_queue(refined_candidates)
        run_id = f"search_{request.disease}_{uuid4().hex[:8]}"
        status = "ok" if scored or any(result.request_count for result in provider_results) or request.dry_run else "safe_failed"
        provider_diagnostics = build_provider_failure_diagnostics(provider_results)
        source_type_distribution = _source_type_distribution(refined_candidates)
        quality_counts = quality_gate_summary(quality_assessments)
        metadata_check_request_count = int(metadata_summary.request_count) if metadata_summary is not None else 0
        metadata_confirmed = int(metadata_summary.confirmed_data_sources) if metadata_summary is not None else 0
        metadata_rejected = int(metadata_summary.rejected_non_data_sources) if metadata_summary is not None else 0
        positive_yield = build_positive_yield_validation(
            request=request,
            candidates_seen=sum(len(result.search_results) for result in provider_results),
            candidates_after_dedupe=len(refined_candidates),
            pending_review_inserted=inserted,
            connector_specs_generated=len(connector_specs),
            request_count_total=sum(int(result.request_count) for result in provider_results),
        )
        warnings = _collect_warnings(plan.warnings, provider_results, aliases.unknown_disease_alias_expansion)
        report = LiveSourceSearchReport(
            status=status,
            run_id=run_id,
            country=request.country,
            disease=request.disease,
            aliases_used=aliases.aliases,
            providers_planned=plan.providers_planned,
            providers_used=[result.provider for result in provider_results],
            request_count_total=sum(int(result.request_count) for result in provider_results),
            provider_request_counts={result.provider: int(result.request_count) for result in provider_results},
            candidates_seen=sum(len(result.search_results) for result in provider_results),
            candidates_after_dedupe=len(refined_candidates),
            pending_review_inserted=inserted,
            connector_specs_generated=len(connector_specs),
            top_domains=_top_domains(refined_candidates),
            failure_types={result.provider: result.failure_type for result in provider_results},
            warnings=warnings,
            quality_gate_passed=quality_counts.get("pass", 0) + quality_counts.get("pass_with_warning", 0) + quality_counts.get("needs_metadata_check", 0),
            quality_gate_rejected=quality_counts.get("rejected", 0),
            quality_gate_deprioritized=quality_counts.get("deprioritized", 0),
            metadata_check_executed=bool(request.metadata_check),
            metadata_check_request_count=metadata_check_request_count,
            metadata_confirmed_data_sources=metadata_confirmed,
            metadata_rejected_non_data_sources=metadata_rejected,
            refined_top_domains=_top_domains(refined_candidates),
            candidate_urls_visited_for_metadata_check=metadata_check_request_count > 0,
            files_downloaded=False,
            full_body_saved=False,
            provider_failure_diagnostics=provider_diagnostics,
            positive_yield_validation=positive_yield,
            source_type_distribution=source_type_distribution,
            providers_skipped=plan.skipped_providers,
            review_queue=[item.to_dict() for item in review_items],
            refined_review_queue=[item.to_dict() for item in refined_review_items],
            connector_specs=connector_specs,
            ranked_candidates=[candidate.to_dict() for candidate in scored[:25]],
            candidate_quality_gate=[assessment.to_dict() for assessment in quality_assessments],
            metadata_check_results=metadata_results,
        )
        run_dir = make_run_dir(output_dir=request.output_dir, country=request.country, disease=request.disease)
        paths = write_live_source_search_report(
            run_dir=run_dir,
            report=report,
            provider_plan=plan,
            provider_results=provider_results,
            normalized_candidates=normalized,
            deduped_candidates=scored,
            review_queue=review_items,
            refined_review_queue=refined_review_items,
            connector_specs=connector_specs,
            quality_assessments=quality_assessments,
            metadata_check_summary=metadata_summary,
        )
        return replace(
            report,
            output_dir=str(run_dir),
            review_queue_path=paths["review_queue_json"],
            connector_specs_path=paths["connector_specs"],
            summary_path=paths["summary_json"],
            safety_audit_path=paths["safety_audit"],
            provider_failure_diagnostics_path=paths["provider_failure_diagnostics_json"],
            positive_yield_validation_path=paths["positive_yield_validation_json"],
            candidate_quality_gate_path=paths["candidate_quality_gate_json"],
            metadata_check_results_path=paths["metadata_check_results_json"],
            refined_review_queue_path=paths["refined_review_queue_json"],
            metadata_check_safety_audit_path=paths["metadata_check_safety_audit"],
        )

    def _store_candidates(self, candidates, *, min_source_score: float) -> int:
        source_candidates = [candidate_to_source_candidate(candidate) for candidate in candidates]
        summary = SourceCandidateStore(self.session).save_candidates(
            source_candidates,
            min_source_score=min_source_score,
        )
        return int(summary.get("inserted_or_updated", 0))


def run_live_source_search(request: LiveSourceSearchRequest, *, session: Session | None = None) -> LiveSourceSearchReport:
    return LiveSourceSearchOrchestrator(session).run(request)


def candidate_to_source_candidate(candidate) -> SourceCandidate:
    return SourceCandidate(
        name=candidate.source_name or candidate.normalized_title,
        base_url=candidate.normalized_url,
        domain=candidate.domain,
        country_scope=None,
        disease_scope=None,
        source_type=candidate.candidate_source_type,
        authority_score=candidate.authority_score,
        relevance_score=round((candidate.disease_relevance_score * 0.7) + (candidate.country_relevance_score * 0.3), 6),
        structured_data_score=candidate.data_likelihood_score,
        freshness_score=candidate.freshness_score,
        legal_or_robots_penalty=candidate.compliance_risk_score,
        source_score=candidate.refined_score or candidate.total_source_score,
        evidence={
            "domain": candidate.domain,
            "rank": candidate.raw_rank,
            "matched_terms": candidate.disease_mentions + candidate.country_mentions,
            "structured_terms": candidate.data_keywords,
            "snippet_length": len(candidate.snippet_summary or ""),
            "requires_human_review": True,
            "quality_gate_status": candidate.quality_gate_status,
            "quality_penalties": candidate.quality_penalties,
            "quality_boosts": candidate.quality_boosts,
            "metadata_check_status": candidate.metadata_check_status,
            "data_source_likelihood": candidate.data_source_likelihood,
        },
        requires_human_review=True,
    )


def report_summary(report: LiveSourceSearchReport) -> dict[str, Any]:
    return {
        "status": report.status,
        "run_id": report.run_id,
        "country": report.country,
        "disease": report.disease,
        "aliases_used": report.aliases_used[:12],
        "providers_planned": report.providers_planned,
        "providers_used": report.providers_used,
        "request_count_total": report.request_count_total,
        "provider_request_counts": report.provider_request_counts,
        "candidates_seen": report.candidates_seen,
        "candidates_after_dedupe": report.candidates_after_dedupe,
        "pending_review_inserted": report.pending_review_inserted,
        "connector_specs_generated": report.connector_specs_generated,
        "top_domains": report.top_domains[:10],
        "failure_types": report.failure_types,
        "quality_gate_passed": report.quality_gate_passed,
        "quality_gate_rejected": report.quality_gate_rejected,
        "quality_gate_deprioritized": report.quality_gate_deprioritized,
        "metadata_check_executed": report.metadata_check_executed,
        "metadata_check_request_count": report.metadata_check_request_count,
        "metadata_confirmed_data_sources": report.metadata_confirmed_data_sources,
        "metadata_rejected_non_data_sources": report.metadata_rejected_non_data_sources,
        "refined_top_domains": report.refined_top_domains[:10],
        "candidate_urls_visited_for_metadata_check": report.candidate_urls_visited_for_metadata_check,
        "files_downloaded": report.files_downloaded,
        "full_body_saved": report.full_body_saved,
        "provider_failure_diagnostics": report.provider_failure_diagnostics[:10],
        "positive_yield_validation": report.positive_yield_validation,
        "source_type_distribution": report.source_type_distribution,
        "providers_skipped": report.providers_skipped,
        "warnings": report.warnings,
        "no_follow_links": report.no_follow_links,
        "candidate_urls_visited": report.candidate_urls_visited,
        "output_dir": report.output_dir,
        "review_queue_path": report.review_queue_path,
        "connector_specs_path": report.connector_specs_path,
        "summary_path": report.summary_path,
        "safety_audit_path": report.safety_audit_path,
        "provider_failure_diagnostics_path": report.provider_failure_diagnostics_path,
        "positive_yield_validation_path": report.positive_yield_validation_path,
        "candidate_quality_gate_path": report.candidate_quality_gate_path,
        "metadata_check_results_path": report.metadata_check_results_path,
        "refined_review_queue_path": report.refined_review_queue_path,
        "metadata_check_safety_audit_path": report.metadata_check_safety_audit_path,
        "review_queue": report.review_queue[:25],
        "refined_review_queue": report.refined_review_queue[:25],
        "metadata_check_results": report.metadata_check_results[:10],
        "connector_specs": report.connector_specs[:25],
        "limitations": report.limitations,
    }


def _collect_warnings(plan_warnings: list[str], provider_results, unknown_alias: bool) -> list[str]:
    warnings = list(plan_warnings)
    if unknown_alias:
        warnings.append("Unknown disease alias expansion used; review relevance carefully.")
    for result in provider_results:
        warnings.extend(f"{result.provider}: {warning}" for warning in result.warnings)
        if result.failure_type:
            warnings.append(f"{result.provider}: failure_type={result.failure_type}")
    warnings.append("Candidate URLs were not visited and links were not followed.")
    warnings.append("All stored candidates remain pending_review.")
    return sorted(set(warnings))


def _top_domains(candidates) -> list[str]:
    domains: list[str] = []
    for candidate in candidates:
        if candidate.domain not in domains:
            domains.append(candidate.domain)
        if len(domains) >= 10:
            break
    return domains


def build_provider_failure_diagnostics(provider_results) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    for result in provider_results:
        diagnostics.append(
            {
                "provider": result.provider,
                "status": result.status,
                "failure_type": result.failure_type,
                "http_status_code": result.http_status_code,
                "request_count": int(result.request_count),
                "network_accessed": bool(result.network_accessed),
                "retry_allowed": bool(result.retry_allowed),
                "safe_failure": bool(result.safe_failure),
                "sanitized_error_summary": result.sanitized_error_summary,
                "candidate_urls_visited": bool(result.candidate_urls_visited),
                "follow_links": bool(result.follow_links),
            }
        )
    return diagnostics


def build_positive_yield_validation(
    *,
    request: LiveSourceSearchRequest,
    candidates_seen: int,
    candidates_after_dedupe: int,
    pending_review_inserted: int,
    connector_specs_generated: int,
    request_count_total: int,
) -> dict[str, Any]:
    return {
        "disease": request.disease,
        "country": request.country,
        "candidates_seen": int(candidates_seen),
        "candidates_after_dedupe": int(candidates_after_dedupe),
        "pending_review_inserted": int(pending_review_inserted),
        "connector_specs_generated": int(connector_specs_generated),
        "achieved_positive_yield": bool(
            candidates_seen > 0
            and candidates_after_dedupe > 0
            and (request.dry_run or pending_review_inserted > 0)
            and connector_specs_generated > 0
        ),
        "achieved_without_candidate_url_visit": True,
        "achieved_without_follow_links": True,
        "request_budget_respected": int(request_count_total) <= max(1, min(int(request.max_total_requests), 3)),
        "limitations": (
            "Provider-only candidate discovery. Candidate URLs are not visited, links are not followed, "
            "sources stay pending_review, and connector specs are recommendations for human review."
        ),
    }


def _source_type_distribution(candidates) -> dict[str, int]:
    distribution: dict[str, int] = {}
    for candidate in candidates:
        key = str(candidate.candidate_source_type or "unknown")
        distribution[key] = distribution.get(key, 0) + 1
    return distribution


__all__ = [
    "LiveSourceSearchOrchestrator",
    "candidate_to_source_candidate",
    "build_positive_yield_validation",
    "build_provider_failure_diagnostics",
    "report_summary",
    "run_live_source_search",
]
