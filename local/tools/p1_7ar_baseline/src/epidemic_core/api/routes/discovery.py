from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from epidemic_core.api.dependencies import get_session
from epidemic_core.discovery.schemas import DiscoveryInput, DiscoveryStart, DiscoveryStatus
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
