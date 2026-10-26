from __future__ import annotations

from datetime import date

from common.db import get_session, init_db
from common.models import ModelExperienceBank, Prediction, PredictionFeedback
from predictor_agent.feedback import PredictionFeedbackInput, PredictionFeedbackService


def _database_url(tmp_path) -> str:
    return f"sqlite:///{tmp_path / 'prediction-feedback.db'}"


def _add_prediction(session, *, value: float = 10.0, outbreak: bool = False) -> Prediction:
    prediction = Prediction(
        country="United States",
        admin1=None,
        disease="norovirus",
        target_date=date(2025, 3, 1),
        iso_week="2025-W08",
        horizon_days=7,
        model_name="moving_average_baseline",
        model_version="v1",
        predicted_value=value,
        lower_bound=max(0.0, value - 2),
        upper_bound=value + 2,
        uncertainty_method="rolling_residual_std_95pct",
        prediction_interval_json={"level": 0.95},
        metrics_json={},
        provenance_json={"predicted_outbreak_label": outbreak},
        review_status="pending",
    )
    session.add(prediction)
    session.flush()
    return prediction


def test_prediction_feedback_creates_feedback_and_experience(tmp_path) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()

    with get_session(database_url) as session:
        prediction = _add_prediction(session, value=10.0, outbreak=False)
        result = PredictionFeedbackService(session).submit_feedback(
            PredictionFeedbackInput(
                prediction_id=prediction.id,
                feedback_status="failure",
                actual_value=14.0,
                actual_outbreak_label=True,
                failure_reason="under_prediction",
                notes="local analyst note",
                reviewer_ref="reviewer-fixture",
            )
        )
        feedback = session.query(PredictionFeedback).one()
        experience = session.query(ModelExperienceBank).one()

    assert result["feedback_created"] is True
    assert result["experience_created"] is True
    assert feedback.feedback_status == "failure"
    assert feedback.actual_value == 14.0
    assert prediction.review_status == "reviewed"
    assert experience.absolute_error == 4.0
    assert experience.signed_error == 4.0
    assert experience.classification_correct is False
    assert experience.recommended_action == "review_under_prediction_features"


def test_prediction_feedback_repeated_submit_updates_existing_rows(tmp_path) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()

    with get_session(database_url) as session:
        prediction = _add_prediction(session, value=10.0)
        service = PredictionFeedbackService(session)
        first = service.submit_feedback(
            PredictionFeedbackInput(
                prediction_id=prediction.id,
                feedback_status="unknown",
            )
        )
        second = service.submit_feedback(
            PredictionFeedbackInput(
                prediction_id=prediction.id,
                feedback_status="partial_success",
                actual_value=11.0,
                failure_reason="timing_error",
            )
        )
        feedback_count = session.query(PredictionFeedback).count()
        experience_count = session.query(ModelExperienceBank).count()
        feedback = session.query(PredictionFeedback).one()
        experience = session.query(ModelExperienceBank).one()

    assert first["feedback_created"] is True
    assert second["feedback_created"] is False
    assert feedback_count == 1
    assert experience_count == 1
    assert feedback.feedback_status == "partial_success"
    assert experience.absolute_error == 1.0
    assert experience.recommended_action == "review_temporal_alignment"


def test_prediction_feedback_summary_is_aggregated(tmp_path) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()

    with get_session(database_url) as session:
        first_prediction = _add_prediction(session, value=10.0)
        second_prediction = _add_prediction(session, value=20.0)
        service = PredictionFeedbackService(session)
        service.submit_feedback(
            PredictionFeedbackInput(
                prediction_id=first_prediction.id,
                feedback_status="success",
                actual_value=10.0,
            )
        )
        service.submit_feedback(
            PredictionFeedbackInput(
                prediction_id=second_prediction.id,
                feedback_status="failure",
                actual_value=16.0,
                failure_reason="over_prediction",
            )
        )
        summary = service.summarize_feedback()

    assert summary["feedback_count"] == 2
    assert summary["by_status"]["success"] == 1
    assert summary["by_status"]["failure"] == 1
    assert summary["mean_absolute_error"] == 2.0
    assert summary["failure_reasons"] == {"over_prediction": 1}
