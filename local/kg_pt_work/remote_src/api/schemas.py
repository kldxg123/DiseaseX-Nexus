from __future__ import annotations

import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict, Field, model_validator

from epidemic_core.retrieval.windows import EXPLICIT_RANGE, WINDOW_KINDS


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class QueryInput(ApiModel):
    country: str = Field(min_length=1, max_length=128)
    disease: str = Field(min_length=1, max_length=128)
    start_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    end_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    time_window_kind: str | None = None
    time_expression: str | None = Field(default=None, min_length=1, max_length=80)
    source_preference: str = "official"

    @model_validator(mode="after")
    def window(self) -> QueryInput:
        explicit = self.start_date is not None or self.end_date is not None
        if explicit and (self.start_date is None or self.end_date is None):
            raise ValueError("both dates are required")
        if not explicit and self.time_window_kind is None and self.time_expression is None:
            raise ValueError("time window is required")
        if self.time_window_kind is not None and self.time_window_kind not in WINDOW_KINDS:
            raise ValueError("unsupported time window")
        if explicit and self.time_window_kind not in (None, EXPLICIT_RANGE):
            raise ValueError("relative and explicit windows are mutually exclusive")
        return self


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
    start_date: dt.date | None
    end_date: dt.date | None
    time_window_kind: str
    duration_iso8601: str | None = None
    source_preference: str
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
    start_date: dt.date | None
    end_date: dt.date | None
    time_window_kind: str
    duration_iso8601: str | None = None
    source_preference: str
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
    requested_start_date: dt.date | None
    requested_end_date: dt.date | None
    time_window_kind: str
    resolved_start: dt.date | None = None
    resolved_end: dt.date | None = None
    source_declared_max_date: dt.date | None = None
    source_data_through: dt.date | None = None
    source_http_last_modified: str | None = None
    retrieved_at: dt.datetime | None = None
    reporting_lag_days: int | None = None
    freshness_status: str = "UNKNOWN"
    cases_new_data_through: dt.date | None = None
    deaths_new_data_through: dt.date | None = None
    cases_cumulative_data_through: dt.date | None = None
    deaths_cumulative_data_through: dt.date | None = None
    cases_freshness_status: str = "NO_DATA"
    deaths_freshness_status: str = "NO_DATA"
    observed_period_count: int = 0
    expected_period_count: int = 0
    missing_period_count: int = 0
    coverage_ratio: float = 0.0
    dashboard_ready: bool = False
    dashboard_url: str | None = None
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
    server_filter_applied: bool = False
    odata_count: int | None = None
    returned_row_count: int | None = None
    date_types: list[str] = []
    all_null_source_row_count: int = 0
    future_empty_placeholder_count: int = 0
    mixed_temporal_granularity: bool = False
    normalized_fields: list[str] = []
    preserved_raw_not_normalized: list[str] = []
