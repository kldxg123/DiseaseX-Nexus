from __future__ import annotations

import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict, Field


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class QueryInput(ApiModel):
    country: str = Field(min_length=1, max_length=128)
    disease: str = Field(min_length=1, max_length=128)
    start_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    end_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


class CanonicalCountry(ApiModel):
    alpha2: str
    alpha3: str
    name: str


class CanonicalDisease(ApiModel):
    id: uuid.UUID
    slug: str
    name: str


class NormalizedResponse(ApiModel):
    country_alpha2: str
    country_alpha3: str
    country_name: str
    disease_id: uuid.UUID
    disease_slug: str
    disease_name: str
    start_date: dt.date
    end_date: dt.date
    fingerprint: str
    schema_version: int
    persisted: bool = False
    retrieval_started: bool = False


class RegisteredResponse(NormalizedResponse):
    query_id: uuid.UUID
    created: bool


class QueryResponse(ApiModel):
    query_id: uuid.UUID
    fingerprint: str
    country_alpha2: str
    country_alpha3: str
    country_name: str
    disease_id: uuid.UUID
    disease_slug: str
    disease_name: str
    start_date: dt.date
    end_date: dt.date
    schema_version: int
    created_at: dt.datetime
    retrieval_started: bool = False


class StartRetrievalBody(ApiModel):
    pass


class RetrievalResponse(ApiModel):
    retrieval_run_id: uuid.UUID
    query_id: uuid.UUID
    source_key: str
    status: str
    attempt_number: int
    created: bool = False
    retried: bool = False
    created_at: dt.datetime
    started_at: dt.datetime | None = None
    finished_at: dt.datetime | None = None
    error_code: str | None = None
    not_modified: bool = False
    artifact_scope: str
    requested_country_alpha2: str
    requested_disease_slug: str
    requested_start_date: dt.date
    requested_end_date: dt.date
    artifact_sha256: str | None = None
    artifact_byte_size: int | None = None
    artifact_media_type: str | None = None
    attribution: str
    source_page_url: str
    query_filter_applied: bool = False
    normalized: bool = False
    normalized_observations_created: bool = False
    retrieval_status: str
    normalization_run_id: uuid.UUID | None = None
    normalization_status: str | None = None
    pipeline_status: str
    result_code: str | None = None
    selected_source_rows: int = 0
    normalized_observation_count: int = 0
    missing_value_count: int = 0
    quality_warning_count: int = 0
    dataset_snapshot_id: uuid.UUID | None = None
    snapshot_status: str
    snapshot_row_count: int = 0
    snapshot_content_sha256: str | None = None
    snapshot_schema_version: int | None = None
    training_format_ready: bool = False
    forecast_readiness_assessed: bool = False
    prediction_performed: bool = False
    terms_url: str
    license_summary: str
