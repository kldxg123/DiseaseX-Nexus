from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class DiscoveryInput(Strict):
    country_iso3: str = Field(pattern=r"^[A-Z]{3}$")
    disease_id: uuid.UUID
    desired_metrics: list[str] = Field(min_length=1, max_length=10)
    time_window_kind: str
    preferred_source_tiers: list[str] = Field(min_length=1, max_length=5)
    language_preferences: list[str] = Field(default_factory=lambda: ["en"])
    max_candidates: int = Field(ge=1, le=20)
    retry_of_run_id: uuid.UUID | None = None
    retry_nonce: uuid.UUID | None = None

    @field_validator("disease_id", "retry_of_run_id", "retry_nonce", mode="before")
    @classmethod
    def parse_uuid(cls, value: object) -> uuid.UUID | None:
        if value is None:
            return None
        if isinstance(value, uuid.UUID):
            return value
        if isinstance(value, str):
            return uuid.UUID(value)
        raise ValueError("invalid disease UUID")


class DiscoveryStart(Strict):
    discovery_run_id: uuid.UUID
    state: str
    created: bool
    review_required: bool = True


class DiscoveryStatus(Strict):
    discovery_run_id: uuid.UUID
    state: str
    candidate_count: int
    review_ready_count: int
    blocked_count: int
    candidates: list[dict[str, object]]
    review_required: bool = True
    review_url: str
