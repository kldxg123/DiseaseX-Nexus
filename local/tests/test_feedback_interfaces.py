from __future__ import annotations

import json
from datetime import date

from common.db import get_session, init_db
from common.models import HumanFeedback as ModelHumanFeedback
from common.models import ModelExperienceBank, Prediction, PredictionFeedback
from predictor_agent import (
    ExperienceBankStore,
    FeedbackInput,
    FeedbackStore,
    HumanFeedback,
    PredictionFeedbackInput,
    PredictionFeedbackService,
    assign_retraining_weight,
    build_lesson_json,
    classify_failure_type,
)


def _database_url(tmp_path) -> str:
    return f"sqlite:///{tmp_path / 'feedback-interfaces.db'}"


def _add_prediction(
    session,
    *,
    value: float = 10.0,
    outbreak: bool = False,
    iso_week: str = "2025-W08",
) -> Prediction:
    prediction = Prediction(
        country="United States",
        admin1=None,
        disease="norovirus",
        target_date=date(2025, 3, 1),
        iso_week=iso_week,
        horizon_days=7,
        model_name="feature_rule",
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


def test_feedback_stable_aliases_keep_old_interfaces() -> None:
    assert HumanFeedback is PredictionFeedback
    assert ModelHumanFeedback is PredictionFeedback
    assert HumanFeedback.__tablename__ == "human_feedback"
    assert FeedbackInput is PredictionFeedbackInput
    assert issubclass(FeedbackStore, PredictionFeedbackService)


def test_feedback_store_submits_and_experience_store_reads(tmp_path) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()

    with get_session(database_url) as session:
        prediction = _add_prediction(session, value=10.0, outbreak=False)
        result = FeedbackStore(session).add_feedback(
            FeedbackInput(
                prediction_id=prediction.id,
                feedback_status="failure",
                actual_value=14.0,
                actual_outbreak_label=True,
                notes="do not expose this note",
            )
        )
        experience_store = ExperienceBankStore(session)
        by_prediction = experience_store.get_for_prediction(prediction.id)
        by_feedback = experience_store.get_for_feedback(result["feedback_id"])
        experiences = experience_store.list_experiences(failure_reason="missed_outbreak")
        summary = experience_store.summarize_experiences()

    assert result["feedback_created"] is True
    assert by_prediction is by_feedback
    assert by_prediction is not None
    assert by_prediction.failure_reason == "missed_outbreak"
    assert by_prediction.experience_json["failure_type"] == "missed_outbreak"
    assert by_prediction.experience_json["auto_retrain_triggered"] is False
    assert by_prediction.experience_json["auto_model_replacement_triggered"] is False
    assert len(experiences) == 1
    assert summary["experience_count"] == 1
    assert summary["by_failure_type"] == {"missed_outbreak": 1}
    assert summary["by_model"] == {"feature_rule": 1}


def test_failure_classification_and_retraining_weight_helpers() -> None:
    assert classify_failure_type(10.0, 14.0) == "under_prediction"
    assert classify_failure_type(10.0, 6.0) == "over_prediction"
    assert classify_failure_type(10.0, 10.0, False, True) == "missed_outbreak"
    assert classify_failure_type(10.0, 10.0, True, False) == "false_alarm"
    assert classify_failure_type(10.0, 14.0, explicit_reason="timing_error") == "timing_error"
    assert classify_failure_type(10.0, 10.0) == "unknown"
    assert assign_retraining_weight("success") == 0.25
    assert assign_retraining_weight("partial_success") == 0.75
    assert assign_retraining_weight("failure", "missed_outbreak") == 1.25
    assert assign_retraining_weight("unknown") == 0.1


def test_build_lesson_json_is_safe_and_serializable(tmp_path) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()

    with get_session(database_url) as session:
        prediction = _add_prediction(session, value=20.0, outbreak=True)
        FeedbackStore(session).submit_feedback(
            FeedbackInput(
                prediction_id=prediction.id,
                feedback_status="failure",
                actual_value=8.0,
                actual_outbreak_label=False,
                failure_reason="false_alarm",
                notes="sensitive analyst note should stay out of lesson json",
            )
        )
        feedback = session.query(PredictionFeedback).one()
        experience = session.query(ModelExperienceBank).one()
        lesson = build_lesson_json(prediction, feedback, experience)
        encoded = json.dumps(lesson, sort_keys=True)

    assert lesson["failure_type"] == "false_alarm"
    assert lesson["recommended_action"] == "review_false_alarm_thresholds"
    assert lesson["retraining_weight"] == 1.35
    assert lesson["auto_retrain_triggered"] is False
    assert lesson["auto_model_replacement_triggered"] is False
    assert "analyst note" not in encoded
