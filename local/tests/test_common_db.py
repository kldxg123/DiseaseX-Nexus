from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import inspect, select

from common.db import get_session, init_db
from common.models import (
    EpidemicEvent,
    DeepSeekWebSearchQuery,
    DeepSeekWebSearchResult,
    DeepSeekWebSearchRun,
    DiseaseCountryInformationRecord,
    ForecastRun,
    HumanFeedback,
    ModelFeature,
    ModelExperienceBank,
    Prediction,
    PredictionFeedback,
    RawDocument,
    RawObservation,
    Source,
    SourceRegistry,
)


def test_init_db_creates_expected_tables() -> None:
    engine = init_db("sqlite:///:memory:")
    inspector = inspect(engine)

    assert {
        "forecast_outputs",
        "forecast_runs",
        "epidemic_events",
        "model_features",
        "model_experience_bank",
        "normalized_observations",
        "predictions",
        "human_feedback",
        "deepseek_web_search_queries",
        "deepseek_web_search_results",
        "deepseek_web_search_runs",
        "disease_country_information_records",
        "raw_documents",
        "raw_observations",
        "review_decisions",
        "source_registry",
        "sources",
    }.issubset(set(inspector.get_table_names()))

    engine.dispose()


def test_get_session_persists_basic_records(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'local.db'}"
    engine = init_db(database_url)
    engine.dispose()

    with get_session(database_url) as session:
        source = Source(name="fixture source", source_type="fixture")
        session.add(source)
        session.flush()
        session.add(
            RawObservation(
                source_id=source.id,
                retrieved_at=datetime.now(UTC),
                payload={"status": "fixture"},
                parser_version="test",
            )
        )
        session.add(
            ForecastRun(
                model_name="baseline",
                model_version="test",
                input_snapshot_id="fixture",
                horizon_days=14,
                run_at=datetime.now(UTC),
                assumptions={"review_required": True},
                metrics={},
            )
        )

    with get_session(database_url) as session:
        source_count = len(session.scalars(select(Source)).all())
        raw_count = len(session.scalars(select(RawObservation)).all())
        forecast_run = session.scalars(select(ForecastRun)).one()

    assert source_count == 1
    assert raw_count == 1
    assert forecast_run.horizon_days == 14
    assert forecast_run.assumptions == {"review_required": True}


def test_model_table_metadata_contains_review_and_uncertainty_fields() -> None:
    forecast_columns = set(ForecastRun.__table__.columns.keys())
    document_columns = set(RawDocument.__table__.columns.keys())
    event_columns = set(EpidemicEvent.__table__.columns.keys())
    feature_columns = set(ModelFeature.__table__.columns.keys())
    prediction_columns = set(Prediction.__table__.columns.keys())
    feedback_columns = set(PredictionFeedback.__table__.columns.keys())
    experience_columns = set(ModelExperienceBank.__table__.columns.keys())
    raw_columns = set(RawObservation.__table__.columns.keys())
    registry_columns = set(SourceRegistry.__table__.columns.keys())
    search_run_columns = set(DeepSeekWebSearchRun.__table__.columns.keys())
    search_query_columns = set(DeepSeekWebSearchQuery.__table__.columns.keys())
    search_result_columns = set(DeepSeekWebSearchResult.__table__.columns.keys())
    info_record_columns = set(DiseaseCountryInformationRecord.__table__.columns.keys())

    assert {"assumptions", "metrics", "horizon_days"}.issubset(forecast_columns)
    assert {
        "source_registry_id",
        "original_url",
        "normalized_url",
        "title",
        "body_text",
        "content_hash",
        "fetched_at",
        "published_at",
        "country_detected",
        "disease_detected",
        "extraction_status",
        "metadata_json",
    }.issubset(document_columns)
    assert {
        "raw_document_id",
        "country",
        "admin1",
        "admin2",
        "disease",
        "event_date",
        "iso_week",
        "cases",
        "suspected_cases",
        "confirmed_cases",
        "deaths",
        "hospitalizations",
        "tests",
        "positivity_rate",
        "outbreaks_count",
        "setting",
        "pathogen_genotype",
        "confidence_score",
        "extraction_method",
        "provenance_json",
        "created_at",
        "updated_at",
    }.issubset(event_columns)
    assert {
        "country",
        "admin1",
        "disease",
        "target_date",
        "iso_week",
        "horizon_days",
        "feature_json",
        "target_value",
        "target_outbreak_label",
        "split_type",
        "created_at",
        "updated_at",
    }.issubset(feature_columns)
    assert {
        "country",
        "admin1",
        "disease",
        "target_date",
        "iso_week",
        "horizon_days",
        "model_name",
        "model_version",
        "predicted_value",
        "lower_bound",
        "upper_bound",
        "uncertainty_method",
        "prediction_interval_json",
        "metrics_json",
        "provenance_json",
        "review_status",
        "created_at",
        "updated_at",
    }.issubset(prediction_columns)
    assert {
        "prediction_id",
        "feedback_status",
        "actual_value",
        "actual_outbreak_label",
        "failure_reason",
        "notes",
        "reviewer_ref",
        "metadata_json",
        "created_at",
        "updated_at",
    }.issubset(feedback_columns)
    assert HumanFeedback.__table__.name == "human_feedback"
    assert HumanFeedback is PredictionFeedback
    assert all(
        hasattr(HumanFeedback, alias)
        for alias in ("reviewer", "verdict", "true_cases", "true_outbreak_label", "reason")
    )
    assert {
        "prediction_id",
        "feedback_id",
        "country",
        "disease",
        "horizon_days",
        "model_name",
        "predicted_value",
        "actual_value",
        "absolute_error",
        "signed_error",
        "classification_correct",
        "feedback_status",
        "failure_reason",
        "experience_json",
        "recommended_action",
    }.issubset(experience_columns)
    assert all(
        hasattr(ModelExperienceBank, alias)
        for alias in ("feature_snapshot_id", "failure_type", "lesson_json", "weight_for_retraining")
    )
    assert {"payload", "parser_version", "review_status"}.issubset(raw_columns)
    assert {
        "status",
        "authority_score",
        "relevance_score",
        "freshness_score",
        "structured_data_score",
        "historical_success_score",
        "language_country_match",
        "legal_or_robots_penalty",
        "consecutive_failures",
        "last_success_at",
        "last_failed_at",
        "metadata_json",
    }.issubset(registry_columns)
    assert {
        "run_id",
        "disease",
        "country",
        "query_intent",
        "search_backend",
        "allow_deepseek_api",
        "allow_general_web_search",
        "allow_page_read",
        "search_request_count",
        "page_read_request_count",
        "deepseek_call_count",
        "records_persisted",
        "warnings_json",
        "safety_summary_json",
    }.issubset(search_run_columns)
    assert {"run_id", "query_text", "query_rank", "query_intent", "generated_by"}.issubset(search_query_columns)
    assert {
        "run_id",
        "provider",
        "title_summary",
        "url",
        "domain",
        "snippet_summary",
        "source_type_hint",
        "total_score",
        "selected_for_page_read",
    }.issubset(search_result_columns)
    assert {
        "run_id",
        "disease",
        "country",
        "source_url",
        "source_domain",
        "information_type",
        "evidence_type",
        "metric_name",
        "metric_value",
        "cleaning_status",
        "feature_ready_potential",
        "requires_human_review",
    }.issubset(info_record_columns)
    assert date(2026, 1, 1).isoformat() == "2026-01-01"
