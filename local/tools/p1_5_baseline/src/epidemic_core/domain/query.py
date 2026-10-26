from __future__ import annotations

import datetime as dt
import hashlib
import json

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class EpidemicQueryInput(StrictModel):
    country: str = Field(min_length=1, max_length=200)
    disease: str = Field(min_length=1, max_length=200)
    start_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    end_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


class CountryResolution(StrictModel):
    alpha2: str = Field(pattern=r"^[A-Z]{2}$")
    normalized_input: str


class DiseaseResolution(StrictModel):
    slug: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    normalized_input: str


class NormalizedEpidemicQuery(StrictModel):
    country_alpha2: str = Field(pattern=r"^[A-Z]{2}$")
    disease_slug: str
    start_date: dt.date
    end_date: dt.date
    schema_version: int = 1

    @model_validator(mode="after")
    def validate_range(self) -> NormalizedEpidemicQuery:
        if self.start_date > self.end_date:
            raise ValueError("start_date must not exceed end_date")
        return self

    def fingerprint(self) -> str:
        payload = self.model_dump(mode="json")
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        return hashlib.sha256(raw).hexdigest()
