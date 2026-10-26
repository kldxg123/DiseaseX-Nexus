from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from epidemic_core.api.dependencies import get_session
from epidemic_core.daily.schemas import (
    DailyRunStart,
    DailyRunStatus,
    DailyScheduleList,
    DailyScheduleStatus,
    ManualDailyUpdateBody,
)
from epidemic_core.daily.service import DailyUpdateError, DailyUpdateService
from epidemic_core.db.models.tables import DailyUpdateRun, DailyUpdateSchedule

router = APIRouter(prefix="/v1/daily-updates", tags=["daily-updates"])
SessionDep = Annotated[Session, Depends(get_session)]


@router.get("", response_model=DailyScheduleList)
def list_daily_updates(session: SessionDep) -> DailyScheduleList:
    rows = session.execute(text("SELECT * FROM epidemic.daily_update_status_v1 ORDER BY source_key,country_alpha2"))
    return DailyScheduleList(schedules=[DailyScheduleStatus(**dict(row)) for row in rows.mappings()])


@router.post("/{schedule_id}/runs", response_model=DailyRunStart, status_code=202)
def start_daily_update(
    schedule_id: uuid.UUID,
    _value: ManualDailyUpdateBody,
    response: Response,
    session: SessionDep,
) -> DailyRunStart:
    schedule = session.get(DailyUpdateSchedule, schedule_id)
    if schedule is None or not schedule.enabled:
        raise HTTPException(404, {"code": "DAILY_SCHEDULE_NOT_AVAILABLE", "message": "Schedule unavailable"})
    before = session.execute(
        select(DailyUpdateRun.run_id).where(
            DailyUpdateRun.schedule_id == schedule_id,
            DailyUpdateRun.logical_date
            == dt.datetime.now(dt.timezone.utc).astimezone(ZoneInfo(schedule.timezone)).date(),
        )
    ).scalar_one_or_none()
    try:
        run_id = DailyUpdateService.start_manual(session, schedule_id, dt.datetime.now(dt.timezone.utc))
        session.commit()
    except DailyUpdateError as exc:
        session.rollback()
        raise HTTPException(409, {"code": str(exc), "message": "Daily update could not be started"}) from None
    run = session.get(DailyUpdateRun, run_id)
    assert run is not None
    created = before is None
    if not created:
        response.status_code = 200
    return DailyRunStart(run_id=run.run_id, schedule_id=schedule_id, state=run.state, created=created)


@router.get("/runs/{run_id}", response_model=DailyRunStatus)
def get_daily_update(run_id: uuid.UUID, session: SessionDep) -> DailyRunStatus:
    run = session.get(DailyUpdateRun, run_id)
    if run is None:
        raise HTTPException(404, {"code": "DAILY_RUN_NOT_FOUND", "message": "Daily update run not found"})
    return DailyRunStatus.model_validate(run, from_attributes=True)
