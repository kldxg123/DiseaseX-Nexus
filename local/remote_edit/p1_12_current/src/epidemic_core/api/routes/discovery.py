from __future__ import annotations

import hashlib
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from epidemic_core.api.dependencies import get_session
from epidemic_core.discovery.providers import PROMPT_INJECTION
from epidemic_core.discovery.safe_fetcher import FetchBlocked, canonicalize, public_addresses
from epidemic_core.discovery.schemas import (
    AgentCandidateReceipt,
    AgentCandidateSubmission,
    DiscoveryInput,
    DiscoveryStart,
    DiscoveryStatus,
)
from epidemic_core.discovery.service import start, status

router = APIRouter(prefix="/v1/source-discovery", tags=["source-discovery"])
SessionDep = Annotated[Session, Depends(get_session)]


@router.post("", response_model=DiscoveryStart, status_code=202)
def start_discovery(value: DiscoveryInput, response: Response, session: SessionDep) -> DiscoveryStart:
    if value.time_window_kind not in {
        "latest_rolling_year",
        "latest_complete_calendar_year",
        "current_year_to_latest_period",
    }:
        raise HTTPException(422, {"code": "INVALID_TIME_WINDOW", "message": "Unsupported time window"})
    if any(x not in {"cases_new", "deaths_new", "incidence_rate"} for x in value.desired_metrics):
        raise HTTPException(422, {"code": "INVALID_METRIC", "message": "Unsupported desired metric"})
    with session.begin_nested():
        run_id, created = start(session, value)
    session.commit()
    if not created:
        response.status_code = 200
    return DiscoveryStart(discovery_run_id=run_id, state="QUEUED", created=created)


@router.get("/{run_id}", response_model=DiscoveryStatus)
def discovery_status(run_id: uuid.UUID, session: SessionDep) -> DiscoveryStatus:
    exists = session.execute(
        text("SELECT 1 FROM epidemic_discovery.discovery_runs WHERE id=:id"), {"id": run_id}
    ).scalar_one_or_none()
    if exists is None:
        raise HTTPException(404, {"code": "DISCOVERY_NOT_FOUND", "message": "Discovery run not found"})
    return DiscoveryStatus(**status(session, run_id))


@router.post("/{run_id}/agent-candidates", response_model=AgentCandidateReceipt, status_code=202)
def submit_agent_candidate(
    run_id: uuid.UUID, value: AgentCandidateSubmission, session: SessionDep
) -> AgentCandidateReceipt:
    if PROMPT_INJECTION.search(value.title + "\n" + value.publisher):
        raise HTTPException(422, {"code": "PROMPT_INJECTION_BLOCKED", "message": "Untrusted instructions rejected"})
    try:
        canonical_url, parsed = canonicalize(value.canonical_url)
        public_addresses(parsed.hostname or "")
    except (FetchBlocked, OSError):
        raise HTTPException(422, {"code": "UNSAFE_CANDIDATE_URL", "message": "URL safety validation failed"}) from None
    url_sha256 = hashlib.sha256(canonical_url.encode()).hexdigest()
    try:
        submission_id = session.execute(
            text(
                "SELECT epidemic_discovery.submit_agent_source_candidate("
                ":run,:url,:url_sha,:domain,:provider,:title,:publisher,:query_sha)"
            ),
            {
                "run": run_id,
                "url": canonical_url,
                "url_sha": url_sha256,
                "domain": parsed.hostname or "",
                "provider": value.provider_id,
                "title": value.title,
                "publisher": value.publisher,
                "query_sha": value.query_sha256,
            },
        ).scalar_one()
        session.commit()
    except Exception as exc:
        session.rollback()
        if "RUN_NOT_ELIGIBLE_FOR_AGENT_SUBMISSION" in str(exc):
            raise HTTPException(409, {"code": "RUN_NOT_ELIGIBLE", "message": "Run is not eligible"}) from None
        raise
    return AgentCandidateReceipt(submission_id=submission_id)
