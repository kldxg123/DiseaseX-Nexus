from __future__ import annotations

import datetime as dt
import hashlib
import json

from pydantic import BaseModel, ConfigDict, Field, model_validator

from epidemic_core.retrieval.windows import EXPLICIT_RANGE, WINDOW_KINDS, parse_time_expression


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class EpidemicQueryInput(StrictModel):
    country: str = Field(min_length=1, max_length=200)
    disease: str = Field(min_length=1, max_length=200)
    start_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    end_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    time_window_kind: str | None = None
    time_expression: str | None = Field(default=None, min_length=1, max_length=80)
    source_preference: str = "official"

    @model_validator(mode="after")
    def validate_window(self) -> EpidemicQueryInput:
        explicit = self.start_date is not None or self.end_date is not None
        if explicit:
            if self.start_date is None or self.end_date is None or self.time_window_kind not in (None, EXPLICIT_RANGE):
                raise ValueError("explicit range requires both dates and no relative window")
        else:
            if self.time_window_kind is None and self.time_expression is None:
                raise ValueError("time window is required")
            kind = self.time_window_kind or parse_time_expression(self.time_expression or "")
            if kind not in WINDOW_KINDS - {EXPLICIT_RANGE}:
                raise ValueError("relative time window required")
        return self


class CountryResolution(StrictModel):
    alpha2: str = Field(pattern=r"^[A-Z]{2}$")
    normalized_input: str


class DiseaseResolution(StrictModel):
    slug: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    normalized_input: str


class NormalizedEpidemicQuery(StrictModel):
    country_alpha2: str = Field(pattern=r"^[A-Z]{2}$")
    disease_slug: str
    start_date: dt.date | None = None
    end_date: dt.date | None = None
    time_window_kind: str = EXPLICIT_RANGE
    duration_iso8601: str | None = None
    source_preference: str = "official"
    schema_version: int = 2

    @model_validator(mode="after")
    def validate_range(self) -> NormalizedEpidemicQuery:
        if self.time_window_kind not in WINDOW_KINDS:
            raise ValueError("unsupported time window")
        if (self.start_date is None) != (self.end_date is None):
            raise ValueError("both dates are required")
        if self.time_window_kind == EXPLICIT_RANGE and (self.start_date is None or self.end_date is None):
            raise ValueError("explicit range requires dates")
        if self.time_window_kind != EXPLICIT_RANGE and (self.start_date is not None or self.end_date is not None):
            raise ValueError("relative time intent must remain unresolved")
        if self.start_date is not None and self.end_date is not None and self.start_date > self.end_date:
            raise ValueError("start_date must not exceed end_date")
        return self

    def fingerprint(self) -> str:
        payload = self.model_dump(mode="json")
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        return hashlib.sha256(raw).hexdigest()
