from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.api.deps import get_db_session
from apps.api.schemas import CleanRunRequest, JobResponse
from cleaner_agent.agent import CleanerAgent
from common.models import EpidemicEvent

router = APIRouter(tags=["clean"])


@router.post("/clean/run", response_model=JobResponse)
def run_cleaner(request: CleanRunRequest, session: Session = Depends(get_db_session)) -> JobResponse:
    summary = CleanerAgent(session).process_pending(
        limit=request.limit,
        country=request.country,
        disease=request.disease,
    )
    return JobResponse(status="ok", summary=summary)


@router.get("/events")
def list_events(
    country: str | None = None,
    disease: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    session: Session = Depends(get_db_session),
) -> dict:
    statement = select(EpidemicEvent).order_by(EpidemicEvent.id).limit(limit)
    if country:
        statement = statement.where(EpidemicEvent.country == country)
    if disease:
        statement = statement.where(EpidemicEvent.disease == disease)
    rows = session.scalars(statement).all()
    return {"events": [_event_summary(row) for row in rows], "count": len(rows)}


def _event_summary(event: EpidemicEvent) -> dict:
    return {
        "id": event.id,
        "raw_document_id": event.raw_document_id,
        "country": event.country,
        "admin1": event.admin1,
        "admin2": event.admin2,
        "disease": event.disease,
        "event_date": event.event_date.isoformat() if event.event_date else None,
        "iso_week": event.iso_week,
        "cases": event.cases,
        "suspected_cases": event.suspected_cases,
        "confirmed_cases": event.confirmed_cases,
        "deaths": event.deaths,
        "hospitalizations": event.hospitalizations,
        "tests": event.tests,
        "outbreaks_count": event.outbreaks_count,
        "setting": event.setting,
        "confidence_score": event.confidence_score,
        "extraction_method": event.extraction_method,
        "provenance_keys": sorted(str(key) for key in (event.provenance_json or {}).keys())[:12],
    }
