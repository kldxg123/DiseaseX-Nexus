from __future__ import annotations

from datetime import UTC, date, datetime

from common.db import get_session, init_db
from common.models import EpidemicEvent, HumanFeedback, ModelExperienceBank, ModelFeature, Prediction, RawDocument, SourceRegistry
from data_quality_agent.profile import build_data_quality_profile


def test_data_quality_profile_summarizes_local_tables(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'quality.db'}"
    init_db(database_url).dispose()
    with get_session(database_url) as session:
        source, raw = _seed_quality_rows(session)
        _ = source, raw
        profile = build_data_quality_profile(session)

    assert profile.source_registry.key_metrics["total"] == 1
    assert profile.raw_documents.key_metrics["unique_content_hashes"] == 1
    assert "duplicate_hash_count" in profile.raw_documents.key_metrics
    assert profile.epidemic_events.key_metrics["missing_country_count"] == 1
    assert profile.model_features.key_metrics["feature_key_coverage"]["current_week_cases"] == 1
    assert profile.predictions.key_metrics["total"] == 1
    assert profile.human_feedback.key_metrics["feedback_total"] == 1
    assert profile.model_experience_bank.key_metrics["experience_total"] == 1
    assert profile.overall_warnings


def _seed_quality_rows(session):
    source = SourceRegistry(
        name="Quality Fixture",
        base_url="https://fixture.invalid/quality",
        country_scope=["United States"],
        disease_scope=["norovirus"],
        source_type="fixture",
        reliability_score=0.8,
        parser_type="fixture",
        status="active",
        metadata_json={"source_score": 0.8},
    )
    session.add(source)
    session.flush()
    raw = RawDocument(
        source_registry_id=source.id,
        original_url="https://fixture.invalid/doc/1",
        normalized_url="https://fixture.invalid/doc/1",
        title="Fixture",
        body_text="short",
        content_hash="hash-quality-1",
        status_code=200,
        content_type="text/plain",
        published_at=datetime(2025, 1, 6, tzinfo=UTC),
        fetched_at=datetime(2025, 1, 6, tzinfo=UTC),
        country_detected="United States",
        disease_detected="norovirus",
        extraction_status="parsed",
        metadata_json={"language": "en"},
    )
    session.add(raw)
    session.flush()
    event = EpidemicEvent(
        raw_document_id=raw.id,
        country=None,
        admin1=None,
        admin2=None,
        disease="norovirus",
        event_date=date(2025, 1, 6),
        iso_week="2025-W02",
        cases=None,
        suspected_cases=None,
        confirmed_cases=None,
        deaths=None,
        hospitalizations=None,
        tests=None,
        positivity_rate=None,
        outbreaks_count=None,
        setting="school",
        pathogen_genotype=None,
        confidence_score=0.3,
        extraction_method="fixture",
        provenance_json={"quality_score": 0.3},
    )
    session.add(event)
    session.add(
        ModelFeature(
            country="United States",
            admin1=None,
            disease="norovirus",
            target_date=date(2025, 1, 13),
            iso_week="2025-W02",
            horizon_days=7,
            feature_json={"current_week_cases": 2, "lag_1w_cases": 1},
            target_value=4.0,
            target_outbreak_label=True,
            split_type="train",
        )
    )
    prediction = Prediction(
        country="United States",
        admin1=None,
        disease="norovirus",
        target_date=date(2025, 1, 20),
        iso_week="2025-W02",
        horizon_days=7,
        model_name="feature_rule",
        model_version="v1",
        predicted_value=3.0,
        lower_bound=1.0,
        upper_bound=5.0,
        uncertainty_method="fixture",
        prediction_interval_json={},
        metrics_json={},
        provenance_json={"risk_level": "medium"},
        review_status="pending",
    )
    session.add(prediction)
    session.flush()
    feedback = HumanFeedback(
        prediction_id=prediction.id,
        feedback_status="failure",
        actual_value=8.0,
        actual_outbreak_label=True,
        failure_reason="under_prediction",
        notes="internal analyst note",
        reviewer_ref="qa",
        metadata_json={},
    )
    session.add(feedback)
    session.flush()
    session.add(
        ModelExperienceBank(
            prediction_id=prediction.id,
            feedback_id=feedback.id,
            country=prediction.country,
            admin1=prediction.admin1,
            disease=prediction.disease,
            target_date=prediction.target_date,
            iso_week=prediction.iso_week,
            horizon_days=prediction.horizon_days,
            model_name=prediction.model_name,
            model_version=prediction.model_version,
            predicted_value=prediction.predicted_value,
            actual_value=8.0,
            absolute_error=5.0,
            signed_error=5.0,
            predicted_outbreak_label=False,
            actual_outbreak_label=True,
            classification_correct=False,
            feedback_status="failure",
            failure_reason="under_prediction",
            experience_json={"failure_type": "under_prediction", "weight_for_retraining": 1.5},
            recommended_action="review_features",
        )
    )
    session.flush()
    return source, raw
