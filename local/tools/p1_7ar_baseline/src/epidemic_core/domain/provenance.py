from __future__ import annotations

import datetime as dt
import uuid
from enum import Enum
from typing import Any

from pydantic import Field

from epidemic_core.domain.query import StrictModel


class RetrievalStatus(str, Enum):
    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    partial = "partial"
    failed = "failed"
    cancelled = "cancelled"


class RetrievalRunCreate(StrictModel):
    run_key: str = Field(min_length=1, max_length=200)
    query_request_id: uuid.UUID
    source_id: uuid.UUID
    adapter_version: str = Field(min_length=1, max_length=100)
    attempt_number: int = Field(ge=1)


class RawArtifactCreate(StrictModel):
    media_type: str = Field(min_length=1, max_length=255)
    encoding: str | None = Field(default=None, max_length=64)
    payload: bytes


class SourceFetchCreate(StrictModel):
    retrieval_run_id: uuid.UUID
    source_id: uuid.UUID
    canonical_url: str = Field(min_length=1, max_length=2048)
    requested_at: dt.datetime
    attempt_number: int = Field(ge=1)
    safe_metadata: dict[str, Any] = Field(default_factory=dict)
