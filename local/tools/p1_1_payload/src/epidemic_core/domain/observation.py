from __future__ import annotations

import datetime as dt
import decimal
import uuid
from typing import Any

from pydantic import Field, model_validator

from epidemic_core.domain.query import StrictModel


class NormalizedObservationCreate(StrictModel):
    record_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    series_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    revision: int = Field(ge=1)
    supersedes_observation_id: uuid.UUID | None = None
    source_fetch_id: uuid.UUID
    country_alpha2: str = Field(pattern=r"^[A-Z]{2}$")
    disease_id: uuid.UUID
    metric_code: str
    value_numeric: decimal.Decimal
    raw_value: str | None = Field(default=None, max_length=500)
    period_start: dt.date
    period_end: dt.date
    granularity: str
    reporting_date: dt.date | None = None
    as_of: dt.datetime
    dimensions: dict[str, Any] = Field(default_factory=dict)
    normalization_version: str = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def range_valid(self) -> "NormalizedObservationCreate":
        if self.period_start > self.period_end:
            raise ValueError("invalid period")
        return self


class QualityAssessmentCreate(StrictModel):
    observation_id: uuid.UUID
    ruleset_version: str = Field(min_length=1, max_length=50)
    status: str
    score: decimal.Decimal = Field(ge=0, le=1)
    flags: list[Any] | dict[str, Any] = Field(default_factory=list)
