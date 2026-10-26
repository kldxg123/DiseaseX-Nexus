from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class SourceRegistry(Base):
    __tablename__ = "source_registry"
    __table_args__ = (
        Index("ix_source_registry_status", "status"),
        Index("ix_source_registry_country_disease", "country_scope", "disease_scope"),
        Index("ix_source_registry_base_url", "base_url", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    country_scope: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    disease_scope: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    source_type: Mapped[str] = mapped_column(String(80), nullable=False)
    authority_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    relevance_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    freshness_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    reliability_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    structured_data_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    historical_success_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    language_country_match: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    legal_or_robots_penalty: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    robots_allowed: Mapped[bool | None] = mapped_column(Boolean)
    parser_type: Mapped[str] = mapped_column(String(80), nullable=False, default="metadata_only")
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="pending_review")
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )

    raw_documents: Mapped[list[RawDocument]] = relationship(back_populates="source_registry")


class DeepSeekWebSearchRun(Base):
    __tablename__ = "deepseek_web_search_runs"
    __table_args__ = (
        Index("ix_deepseek_web_search_runs_run_id", "run_id", unique=True),
        Index("ix_deepseek_web_search_runs_disease_country", "disease", "country"),
        Index("ix_deepseek_web_search_runs_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(160), nullable=False)
    disease: Mapped[str] = mapped_column(String(120), nullable=False)
    country: Mapped[str] = mapped_column(String(120), nullable=False)
    query_intent: Mapped[str] = mapped_column(String(80), nullable=False)
    search_backend: Mapped[str] = mapped_column(String(80), nullable=False)
    deepseek_model: Mapped[str | None] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="planned")
    allow_deepseek_api: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    allow_general_web_search: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    allow_page_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    search_query_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    search_request_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    page_read_request_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deepseek_call_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_extracted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_persisted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    warnings_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    safety_summary_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class DeepSeekWebSearchQuery(Base):
    __tablename__ = "deepseek_web_search_queries"
    __table_args__ = (
        Index("ix_deepseek_web_search_queries_run", "run_id"),
        Index("ix_deepseek_web_search_queries_rank", "run_id", "query_rank"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(160), nullable=False)
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    query_rank: Mapped[int] = mapped_column(Integer, nullable=False)
    query_intent: Mapped[str] = mapped_column(String(80), nullable=False)
    generated_by: Mapped[str] = mapped_column(String(80), nullable=False, default="fallback")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class DeepSeekWebSearchResult(Base):
    __tablename__ = "deepseek_web_search_results"
    __table_args__ = (
        Index("ix_deepseek_web_search_results_run", "run_id"),
        Index("ix_deepseek_web_search_results_domain", "domain"),
        Index("ix_deepseek_web_search_results_score", "run_id", "total_score"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(160), nullable=False)
    query_id: Mapped[int | None] = mapped_column(Integer)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    title_summary: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    domain: Mapped[str] = mapped_column(String(200), nullable=False)
    snippet_summary: Mapped[str | None] = mapped_column(Text)
    published_date: Mapped[str | None] = mapped_column(String(40))
    source_type_hint: Mapped[str | None] = mapped_column(String(80))
    relevance_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    structured_data_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    total_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    selected_for_page_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class DiseaseCountryInformationRecord(Base):
    __tablename__ = "disease_country_information_records"
    __table_args__ = (
        Index("ix_disease_country_records_disease", "disease"),
        Index("ix_disease_country_records_country", "country"),
        Index("ix_disease_country_records_disease_country", "disease", "country"),
        Index("ix_disease_country_records_date", "disease", "country", "event_date"),
        Index("ix_disease_country_records_week", "disease", "country", "iso_week"),
        Index("ix_disease_country_records_run", "run_id"),
        Index("ix_disease_country_records_domain", "source_domain"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(160), nullable=False)
    disease: Mapped[str] = mapped_column(String(120), nullable=False)
    country: Mapped[str] = mapped_column(String(120), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_domain: Mapped[str] = mapped_column(String(200), nullable=False)
    source_title: Mapped[str | None] = mapped_column(Text)
    information_type: Mapped[str] = mapped_column(String(80), nullable=False)
    evidence_type: Mapped[str] = mapped_column(String(80), nullable=False)
    source_type: Mapped[str | None] = mapped_column(String(80))
    event_date: Mapped[date | None] = mapped_column(Date)
    iso_week: Mapped[str | None] = mapped_column(String(10))
    region_name: Mapped[str | None] = mapped_column(String(160))
    metric_name: Mapped[str | None] = mapped_column(String(120))
    metric_value: Mapped[float | None] = mapped_column(Float)
    metric_unit: Mapped[str | None] = mapped_column(String(80))
    text_summary: Mapped[str | None] = mapped_column(Text)
    evidence_summary: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    uncertainty_reason: Mapped[str | None] = mapped_column(Text)
    cleaning_status: Mapped[str] = mapped_column(String(80), nullable=False, default="not_sent")
    raw_document_id: Mapped[int | None] = mapped_column(Integer)
    epidemic_event_id: Mapped[int | None] = mapped_column(Integer)
    feature_ready_potential: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    requires_human_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class RawDocument(Base):
    __tablename__ = "raw_documents"
    __table_args__ = (
        Index("ix_raw_documents_source_url", "source_registry_id", "normalized_url", unique=True),
        Index("ix_raw_documents_content_hash", "content_hash", unique=True),
        Index("ix_raw_documents_fetched_at", "fetched_at"),
        Index("ix_raw_documents_extraction_status", "extraction_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_registry_id: Mapped[int] = mapped_column(
        ForeignKey("source_registry.id"),
        nullable=False,
    )
    original_url: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_url: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    body_text: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status_code: Mapped[int | None] = mapped_column(Integer)
    content_type: Mapped[str | None] = mapped_column(String(200))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    country_detected: Mapped[str | None] = mapped_column(String(120))
    disease_detected: Mapped[str | None] = mapped_column(String(120))
    extraction_status: Mapped[str] = mapped_column(String(40), nullable=False, default="pending")
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

    source_registry: Mapped[SourceRegistry] = relationship(back_populates="raw_documents")
    epidemic_events: Mapped[list[EpidemicEvent]] = relationship(back_populates="raw_document")


class EpidemicEvent(Base):
    __tablename__ = "epidemic_events"
    __table_args__ = (
        Index("ix_epidemic_events_raw_document", "raw_document_id", unique=True),
        Index("ix_epidemic_events_disease_country_date", "disease", "country", "event_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    raw_document_id: Mapped[int] = mapped_column(
        ForeignKey("raw_documents.id"),
        nullable=False,
    )
    country: Mapped[str | None] = mapped_column(String(120))
    admin1: Mapped[str | None] = mapped_column(String(120))
    admin2: Mapped[str | None] = mapped_column(String(120))
    disease: Mapped[str | None] = mapped_column(String(120))
    event_date: Mapped[date | None] = mapped_column(Date)
    iso_week: Mapped[str | None] = mapped_column(String(10))
    cases: Mapped[int | None] = mapped_column(Integer)
    suspected_cases: Mapped[int | None] = mapped_column(Integer)
    confirmed_cases: Mapped[int | None] = mapped_column(Integer)
    deaths: Mapped[int | None] = mapped_column(Integer)
    hospitalizations: Mapped[int | None] = mapped_column(Integer)
    tests: Mapped[int | None] = mapped_column(Integer)
    positivity_rate: Mapped[float | None] = mapped_column(Float)
    outbreaks_count: Mapped[int | None] = mapped_column(Integer)
    setting: Mapped[str | None] = mapped_column(String(120))
    pathogen_genotype: Mapped[str | None] = mapped_column(String(120))
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    extraction_method: Mapped[str] = mapped_column(String(80), nullable=False)
    provenance_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )

    raw_document: Mapped[RawDocument] = relationship(back_populates="epidemic_events")


class ModelFeature(Base):
    __tablename__ = "model_features"
    __table_args__ = (
        Index(
            "ix_model_features_business_key",
            "country",
            "admin1",
            "disease",
            "iso_week",
            "horizon_days",
            unique=True,
        ),
        Index("ix_model_features_target", "disease", "country", "target_date"),
        Index("ix_model_features_split", "split_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    country: Mapped[str] = mapped_column(String(120), nullable=False)
    admin1: Mapped[str | None] = mapped_column(String(120))
    disease: Mapped[str] = mapped_column(String(120), nullable=False)
    target_date: Mapped[date] = mapped_column(Date, nullable=False)
    iso_week: Mapped[str] = mapped_column(String(10), nullable=False)
    horizon_days: Mapped[int] = mapped_column(Integer, nullable=False)
    feature_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    target_value: Mapped[float] = mapped_column(Float, nullable=False)
    target_outbreak_label: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    split_type: Mapped[str] = mapped_column(String(40), nullable=False, default="train")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    source_type: Mapped[str] = mapped_column(String(80), nullable=False)
    base_url: Mapped[str | None] = mapped_column(Text)
    robots_txt_url: Mapped[str | None] = mapped_column(Text)
    terms_url: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )

    raw_observations: Mapped[list[RawObservation]] = relationship(back_populates="source")


class RawObservation(Base):
    __tablename__ = "raw_observations"
    __table_args__ = (
        Index("ix_raw_observations_source_retrieved", "source_id", "retrieved_at"),
        Index("ix_raw_observations_content_hash", "content_hash"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(200))
    source_url: Mapped[str | None] = mapped_column(Text)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    content_hash: Mapped[str | None] = mapped_column(String(128))
    parser_version: Mapped[str] = mapped_column(String(80), nullable=False, default="unknown")
    review_status: Mapped[str] = mapped_column(String(40), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

    source: Mapped[Source] = relationship(back_populates="raw_observations")
    normalized_observations: Mapped[list[NormalizedObservation]] = relationship(
        back_populates="raw_observation",
    )


class NormalizedObservation(Base):
    __tablename__ = "normalized_observations"
    __table_args__ = (
        Index("ix_normalized_observations_disease_date", "disease", "observed_on"),
        Index("ix_normalized_observations_location_date", "country", "admin1", "observed_on"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    raw_observation_id: Mapped[int] = mapped_column(
        ForeignKey("raw_observations.id"),
        nullable=False,
    )
    disease: Mapped[str] = mapped_column(String(120), nullable=False)
    country: Mapped[str] = mapped_column(String(120), nullable=False)
    admin1: Mapped[str | None] = mapped_column(String(120))
    admin2: Mapped[str | None] = mapped_column(String(120))
    observed_on: Mapped[date] = mapped_column(Date, nullable=False)
    metric_name: Mapped[str] = mapped_column(String(120), nullable=False)
    metric_value: Mapped[float] = mapped_column(Float, nullable=False)
    unit: Mapped[str] = mapped_column(String(80), nullable=False, default="count")
    source_trace: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    quality_flags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    review_status: Mapped[str] = mapped_column(String(40), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

    raw_observation: Mapped[RawObservation] = relationship(
        back_populates="normalized_observations",
    )


class ForecastRun(Base):
    __tablename__ = "forecast_runs"
    __table_args__ = (Index("ix_forecast_runs_model_version", "model_name", "model_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    model_name: Mapped[str] = mapped_column(String(120), nullable=False)
    model_version: Mapped[str] = mapped_column(String(120), nullable=False)
    input_snapshot_id: Mapped[str] = mapped_column(String(160), nullable=False)
    horizon_days: Mapped[int] = mapped_column(Integer, nullable=False)
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    assumptions: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

    outputs: Mapped[list[ForecastOutput]] = relationship(back_populates="forecast_run")


class ForecastOutput(Base):
    __tablename__ = "forecast_outputs"
    __table_args__ = (
        Index("ix_forecast_outputs_target", "disease", "country", "target_date"),
        Index("ix_forecast_outputs_run", "forecast_run_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    forecast_run_id: Mapped[int] = mapped_column(ForeignKey("forecast_runs.id"), nullable=False)
    disease: Mapped[str] = mapped_column(String(120), nullable=False)
    country: Mapped[str] = mapped_column(String(120), nullable=False)
    admin1: Mapped[str | None] = mapped_column(String(120))
    target_date: Mapped[date] = mapped_column(Date, nullable=False)
    predicted_value: Mapped[float] = mapped_column(Float, nullable=False)
    lower_bound: Mapped[float] = mapped_column(Float, nullable=False)
    upper_bound: Mapped[float] = mapped_column(Float, nullable=False)
    uncertainty_method: Mapped[str] = mapped_column(String(120), nullable=False)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    review_status: Mapped[str] = mapped_column(String(40), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

    forecast_run: Mapped[ForecastRun] = relationship(back_populates="outputs")


class Prediction(Base):
    __tablename__ = "predictions"
    __table_args__ = (
        Index(
            "ix_predictions_business_key",
            "country",
            "admin1",
            "disease",
            "iso_week",
            "horizon_days",
            "model_name",
            "model_version",
            unique=True,
        ),
        Index("ix_predictions_target", "disease", "country", "target_date"),
        Index("ix_predictions_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    country: Mapped[str] = mapped_column(String(120), nullable=False)
    admin1: Mapped[str | None] = mapped_column(String(120))
    disease: Mapped[str] = mapped_column(String(120), nullable=False)
    target_date: Mapped[date] = mapped_column(Date, nullable=False)
    iso_week: Mapped[str] = mapped_column(String(10), nullable=False)
    horizon_days: Mapped[int] = mapped_column(Integer, nullable=False)
    model_name: Mapped[str] = mapped_column(String(120), nullable=False)
    model_version: Mapped[str] = mapped_column(String(120), nullable=False)
    predicted_value: Mapped[float] = mapped_column(Float, nullable=False)
    lower_bound: Mapped[float] = mapped_column(Float, nullable=False)
    upper_bound: Mapped[float] = mapped_column(Float, nullable=False)
    uncertainty_method: Mapped[str] = mapped_column(String(120), nullable=False)
    prediction_interval_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    metrics_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    provenance_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    review_status: Mapped[str] = mapped_column(String(40), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )
    feedback: Mapped[HumanFeedback | None] = relationship(back_populates="prediction")
    experiences: Mapped[list[ModelExperienceBank]] = relationship(back_populates="prediction")


class HumanFeedback(Base):
    __tablename__ = "human_feedback"
    __table_args__ = (
        Index("ix_prediction_feedback_prediction", "prediction_id", unique=True),
        Index("ix_prediction_feedback_status", "feedback_status"),
        Index("ix_prediction_feedback_failure_reason", "failure_reason"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    prediction_id: Mapped[int] = mapped_column(ForeignKey("predictions.id"), nullable=False)
    feedback_status: Mapped[str] = mapped_column(String(40), nullable=False)
    actual_value: Mapped[float | None] = mapped_column(Float)
    actual_outbreak_label: Mapped[bool | None] = mapped_column(Boolean)
    failure_reason: Mapped[str | None] = mapped_column(String(80))
    notes: Mapped[str | None] = mapped_column(Text)
    reviewer_ref: Mapped[str | None] = mapped_column(String(160))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )

    prediction: Mapped[Prediction] = relationship(back_populates="feedback")
    experience: Mapped[ModelExperienceBank | None] = relationship(back_populates="feedback")

    @property
    def reviewer(self) -> str | None:
        return self.reviewer_ref

    @reviewer.setter
    def reviewer(self, value: str | None) -> None:
        self.reviewer_ref = value

    @property
    def verdict(self) -> str:
        if self.feedback_status == "failure":
            return "fail"
        if self.feedback_status == "partial_success":
            return "partial"
        return self.feedback_status

    @verdict.setter
    def verdict(self, value: str) -> None:
        normalized = (value or "").strip().lower()
        if normalized == "fail":
            self.feedback_status = "failure"
        elif normalized == "partial":
            self.feedback_status = "partial_success"
        else:
            self.feedback_status = normalized

    @property
    def true_cases(self) -> float | None:
        return self.actual_value

    @true_cases.setter
    def true_cases(self, value: float | None) -> None:
        self.actual_value = value

    @property
    def true_outbreak_label(self) -> bool | None:
        return self.actual_outbreak_label

    @true_outbreak_label.setter
    def true_outbreak_label(self, value: bool | None) -> None:
        self.actual_outbreak_label = value

    @property
    def reason(self) -> str | None:
        return self.failure_reason

    @reason.setter
    def reason(self, value: str | None) -> None:
        self.failure_reason = value


PredictionFeedback = HumanFeedback


class ModelExperienceBank(Base):
    __tablename__ = "model_experience_bank"
    __table_args__ = (
        Index("ix_model_experience_prediction", "prediction_id", unique=True),
        Index("ix_model_experience_feedback", "feedback_id", unique=True),
        Index("ix_model_experience_model", "model_name", "model_version"),
        Index("ix_model_experience_target", "disease", "country", "target_date"),
        Index("ix_model_experience_failure", "failure_reason"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    prediction_id: Mapped[int] = mapped_column(ForeignKey("predictions.id"), nullable=False)
    feedback_id: Mapped[int] = mapped_column(ForeignKey("human_feedback.id"), nullable=False)
    country: Mapped[str] = mapped_column(String(120), nullable=False)
    admin1: Mapped[str | None] = mapped_column(String(120))
    disease: Mapped[str] = mapped_column(String(120), nullable=False)
    target_date: Mapped[date] = mapped_column(Date, nullable=False)
    iso_week: Mapped[str] = mapped_column(String(10), nullable=False)
    horizon_days: Mapped[int] = mapped_column(Integer, nullable=False)
    model_name: Mapped[str] = mapped_column(String(120), nullable=False)
    model_version: Mapped[str] = mapped_column(String(120), nullable=False)
    predicted_value: Mapped[float] = mapped_column(Float, nullable=False)
    actual_value: Mapped[float | None] = mapped_column(Float)
    absolute_error: Mapped[float | None] = mapped_column(Float)
    signed_error: Mapped[float | None] = mapped_column(Float)
    predicted_outbreak_label: Mapped[bool | None] = mapped_column(Boolean)
    actual_outbreak_label: Mapped[bool | None] = mapped_column(Boolean)
    classification_correct: Mapped[bool | None] = mapped_column(Boolean)
    feedback_status: Mapped[str] = mapped_column(String(40), nullable=False)
    failure_reason: Mapped[str | None] = mapped_column(String(80))
    experience_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    recommended_action: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )

    prediction: Mapped[Prediction] = relationship(back_populates="experiences")
    feedback: Mapped[HumanFeedback] = relationship(back_populates="experience")

    @property
    def feature_snapshot_id(self) -> str | None:
        payload = self.experience_json if isinstance(self.experience_json, dict) else {}
        value = payload.get("feature_snapshot_id")
        return None if value is None else str(value)

    @feature_snapshot_id.setter
    def feature_snapshot_id(self, value: str | None) -> None:
        payload = dict(self.experience_json or {})
        payload["feature_snapshot_id"] = value
        self.experience_json = payload

    @property
    def failure_type(self) -> str | None:
        payload = self.experience_json if isinstance(self.experience_json, dict) else {}
        return payload.get("failure_type") or self.failure_reason

    @failure_type.setter
    def failure_type(self, value: str | None) -> None:
        self.failure_reason = value
        payload = dict(self.experience_json or {})
        payload["failure_type"] = value
        self.experience_json = payload

    @property
    def lesson_json(self) -> dict[str, Any]:
        return self.experience_json

    @lesson_json.setter
    def lesson_json(self, value: dict[str, Any]) -> None:
        self.experience_json = value

    @property
    def weight_for_retraining(self) -> float | None:
        payload = self.experience_json if isinstance(self.experience_json, dict) else {}
        value = payload.get("weight_for_retraining", payload.get("retraining_weight"))
        return None if value is None else float(value)

    @weight_for_retraining.setter
    def weight_for_retraining(self, value: float | None) -> None:
        payload = dict(self.experience_json or {})
        payload["weight_for_retraining"] = value
        payload["retraining_weight"] = value
        self.experience_json = payload


class ReviewDecision(Base):
    __tablename__ = "review_decisions"
    __table_args__ = (Index("ix_review_decisions_entity", "entity_type", "entity_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    reviewer_ref: Mapped[str | None] = mapped_column(String(160))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

