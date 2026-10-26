from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from crawler_agent.source_registry import STATUS_PENDING_REVIEW, SourceRegistryService
from source_discovery_agent.types import SourceCandidate


class SourceCandidateStore:
    def __init__(self, session: Session):
        self.session = session
        self.registry = SourceRegistryService(session)

    def save_candidates(
        self,
        candidates: list[SourceCandidate],
        *,
        min_source_score: float = 0.35,
    ) -> dict:
        inserted_or_updated = 0
        skipped_low_score = 0
        for candidate in candidates:
            if candidate.source_score < min_source_score:
                skipped_low_score += 1
                continue
            self.registry.upsert_source(
                name=candidate.name,
                base_url=candidate.base_url,
                country_scope=[candidate.country_scope] if candidate.country_scope else ["global"],
                disease_scope=[candidate.disease_scope] if candidate.disease_scope else ["all"],
                source_type=candidate.source_type,
                authority_score=candidate.authority_score,
                relevance_score=candidate.relevance_score,
                freshness_score=candidate.freshness_score,
                reliability_score=candidate.authority_score,
                structured_data_score=candidate.structured_data_score,
                historical_success_score=0.45,
                language_country_match=0.9,
                legal_or_robots_penalty=candidate.legal_or_robots_penalty,
                robots_allowed=None,
                parser_type=_parser_type(candidate),
                status=STATUS_PENDING_REVIEW,
                metadata_json=_candidate_metadata(candidate),
            )
            inserted_or_updated += 1
        return {
            "seen": len(candidates),
            "inserted_or_updated": inserted_or_updated,
            "skipped_low_score": skipped_low_score,
            "pending_review": inserted_or_updated,
        }


def _candidate_metadata(candidate: SourceCandidate) -> dict[str, Any]:
    return {
        "discovered_by": "source_discovery_agent",
        "source_score": candidate.source_score,
        "evidence": _safe_evidence(candidate.evidence),
        "requires_human_review": True,
        "network_executed": False,
        "auto_activate": False,
        "auto_connector_generation": False,
    }


def _safe_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    allowed = {"domain", "rank", "matched_terms", "snippet_length", "structured_terms", "requires_human_review"}
    result: dict[str, Any] = {}
    for key, value in evidence.items():
        if key not in allowed:
            continue
        if isinstance(value, list):
            result[key] = [str(item)[:80] for item in value[:12]]
        elif isinstance(value, (str, int, float, bool)) or value is None:
            result[key] = value
    return result


def _parser_type(candidate: SourceCandidate) -> str:
    terms = set(candidate.evidence.get("structured_terms") or [])
    if "csv" in terms:
        return "csv_dataset"
    if "api" in terms:
        return "json_api"
    if "table" in terms or "dashboard" in terms:
        return "html_table"
    if candidate.source_type == "news":
        return "news_article"
    return "metadata_only"
