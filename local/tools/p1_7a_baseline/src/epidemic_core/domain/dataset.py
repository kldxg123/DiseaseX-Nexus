from __future__ import annotations

import uuid

from pydantic import Field

from epidemic_core.domain.query import StrictModel


class DatasetSnapshotResult(StrictModel):
    snapshot_id: uuid.UUID
    row_count: int = Field(ge=0)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
