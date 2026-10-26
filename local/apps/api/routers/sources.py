from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.api.deps import get_db_session
from apps.api.schemas import JobResponse, MockGdeltDiscoveryRequest, SourceReviewRequest, SourceSeedRequest
from common.models import SourceRegistry
from crawler_agent.default_sources import get_default_seed_sources
from crawler_agent.gdelt import parse_gdelt_articles
from crawler_agent.source_discovery import candidates_from_gdelt_articles
from crawler_agent.source_registry import SourceRegistryService

router = APIRouter(tags=["sources"])


@router.get("/sources")
def list_sources(
    status: str | None = None,
    country: str | None = None,
    disease: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    session: Session = Depends(get_db_session),
) -> dict:
    statement = select(SourceRegistry).order_by(SourceRegistry.id).limit(limit)
    if status:
        statement = statement.where(SourceRegistry.status == status)
    rows = session.scalars(statement).all()
    filtered = [
        source
        for source in rows
        if _scope_matches(source.country_scope, country) and _scope_matches(source.disease_scope, disease)
    ]
    return {
        "sources": [_source_summary(source) for source in filtered],
        "count": len(filtered),
    }


@router.get("/sources/health")
def sources_health(session: Session = Depends(get_db_session)) -> dict:
    rows = session.scalars(select(SourceRegistry)).all()
    counts = Counter(source.status for source in rows)
    return {
        "total": len(rows),
        "by_status": {
            "active": counts.get("active", 0),
            "pending_review": counts.get("pending_review", 0),
            "degraded": counts.get("degraded", 0),
            "disabled": counts.get("disabled", 0),
        },
        "recent_failed_sources": sum(1 for source in rows if source.last_failed_at is not None),
        "with_consecutive_failures": sum(1 for source in rows if source.consecutive_failures > 0),
    }


@router.post("/sources/{source_id}/review")
def review_source(
    source_id: int,
    request: SourceReviewRequest,
    session: Session = Depends(get_db_session),
) -> dict:
    service = SourceRegistryService(session)
    decision = request.decision
    metadata = {
        "review_decision": decision,
        "reviewed_at": datetime.now(UTC).isoformat(),
        "reviewer_provided": bool(request.reviewer),
        "notes_provided": bool(request.notes),
        "terms_reviewed": bool(request.terms_reviewed),
        "manual_reviewed": bool(request.manual_reviewed),
    }
    try:
        if decision == "activate":
            if not request.terms_reviewed or not request.manual_reviewed:
                raise HTTPException(
                    status_code=400,
                    detail="terms_reviewed and manual_reviewed are required before activating a source.",
                )
            source = service.update_health(
                source_id,
                status="active",
                robots_allowed=True,
                metadata_json=metadata,
            )
        elif decision == "disable":
            source = service.disable_source(source_id, reason=request.notes or "manual review")
            source = service.update_health(source.id, metadata_json=metadata)
        else:
            source = service.update_health(source_id, status="pending_review", metadata_json=metadata)
        session.commit()
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "status": "ok",
        "source": _source_summary(source),
        "review": {
            "decision": decision,
            "terms_reviewed": bool(request.terms_reviewed),
            "manual_reviewed": bool(request.manual_reviewed),
            "notes_recorded": bool(request.notes),
            "limitations": "Review updates source status only; it does not crawl the source or deploy a connector.",
        },
    }


@router.post("/sources/seed", response_model=JobResponse)
def seed_sources(request: SourceSeedRequest, session: Session = Depends(get_db_session)) -> JobResponse:
    seeds = get_default_seed_sources()
    if request.dry_run:
        return JobResponse(
            status="ok",
            summary={"dry_run": True, "candidate_count": len(seeds), "created": 0, "updated": 0},
        )
    service = SourceRegistryService(session)
    created = updated = 0
    for seed in seeds:
        existed = service.get_source_by_base_url(seed["base_url"]) is not None
        service.upsert_source(**seed)
        if existed:
            updated += 1
        else:
            created += 1
    return JobResponse(status="ok", summary={"dry_run": False, "created": created, "updated": updated})


@router.post("/sources/discover/gdelt/mock", response_model=JobResponse)
def discover_gdelt_mock(
    request: MockGdeltDiscoveryRequest,
    session: Session = Depends(get_db_session),
) -> JobResponse:
    return run_gdelt_mock_discovery(request, session)


def run_gdelt_mock_discovery(request: MockGdeltDiscoveryRequest, session: Session) -> JobResponse:
    articles = parse_gdelt_articles(request.mock_response_json)
    candidates = candidates_from_gdelt_articles(
        articles=articles,
        country=request.country,
        disease=request.disease,
        query_url=None,
    )
    selected = [candidate for candidate in candidates if candidate.source_score >= request.min_source_score]
    service = SourceRegistryService(session)
    created = updated = 0
    for candidate in selected:
        existed = service.get_source_by_base_url(candidate.base_url) is not None
        service.upsert_source(**candidate.source_payload)
        if existed:
            updated += 1
        else:
            created += 1
    return JobResponse(
        status="ok",
        summary={
            "article_count": len(articles),
            "candidate_count": len(candidates),
            "upserted": len(selected),
            "created": created,
            "updated": updated,
        },
    )


def _source_summary(source: SourceRegistry) -> dict:
    metadata = source.metadata_json if isinstance(source.metadata_json, dict) else {}
    return {
        "id": source.id,
        "name": source.name,
        "base_url": source.base_url,
        "country_scope": source.country_scope,
        "disease_scope": source.disease_scope,
        "source_type": source.source_type,
        "parser_type": source.parser_type,
        "status": source.status,
        "robots_allowed": source.robots_allowed,
        "source_score": metadata.get("source_score"),
        "consecutive_failures": source.consecutive_failures,
        "last_success_at": source.last_success_at.isoformat() if source.last_success_at else None,
        "last_failed_at": source.last_failed_at.isoformat() if source.last_failed_at else None,
        "metadata_keys": sorted(str(key) for key in metadata.keys())[:20],
    }


def _scope_matches(scope: list[str] | None, value: str | None) -> bool:
    if value is None or not scope:
        return True
    normalized_scope = {str(item).lower() for item in scope}
    normalized_value = value.lower()
    return normalized_value in normalized_scope or "global" in normalized_scope or "all" in normalized_scope
