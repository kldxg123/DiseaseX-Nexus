from __future__ import annotations

import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict


class DailyModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ManualDailyUpdateBody(DailyModel):
    pass


class DailyRunStart(DailyModel):
    run_id: uuid.UUID
    schedule_id: uuid.UUID
    state: str
    created: bool


class DailyRunStatus(DailyModel):
    run_id: uuid.UUID
    schedule_id: uuid.UUID
    logical_date: dt.date
    scheduled_for: dt.datetime
    trigger_type: str
    state: str
    missed_intervals: int
    retry_count: int
    max_retries: int
    retry_not_before: dt.datetime | None
    retrieval_run_id: uuid.UUID | None
    normalization_run_id: uuid.UUID | None
    snapshot_id: uuid.UUID | None
    source_changed: bool | None
    observations_added: int
    observations_revised: int
    rows_excluded: int
    final_reason_code: str | None
    started_at: dt.datetime | None
    finished_at: dt.datetime | None


class DailyScheduleStatus(DailyModel):
    schedule_id: uuid.UUID
    schedule_name: str
    enabled: bool
    country_alpha2: str
    country_alpha3: str
    country_name: str
    disease_slug: str
    disease_name: str
    source_key: str
    source_name: str
    timezone: str
    cron_expression: str
    local_execution_time: dt.time
    next_due_at: dt.datetime
    last_checked_at: dt.datetime | None
    last_success_at: dt.datetime | None
    last_source_change_at: dt.datetime | None
    source_data_through: dt.date | None
    normalized_data_through: dt.date | None
    consecutive_failures: int
    run_id: uuid.UUID | None
    logical_date: dt.date | None
    trigger_type: str | None
    state: str | None
    display_state: str
    missed_intervals: int | None
    retry_count: int | None
    max_retries: int | None
    retry_not_before: dt.datetime | None
    source_changed: bool | None
    observations_added: int | None
    observations_revised: int | None
    rows_excluded: int | None
    final_reason_code: str | None
    started_at: dt.datetime | None
    finished_at: dt.datetime | None
    snapshot_id: uuid.UUID | None


class DailyScheduleList(DailyModel):
    schedules: list[DailyScheduleStatus]
