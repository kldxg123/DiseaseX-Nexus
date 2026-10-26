from __future__ import annotations

from datetime import date

from common.db import get_session, init_db
from common.models import HumanFeedback, ModelExperienceBank, Prediction
from predictor_agent.experience_bank import ExperienceBankService
from predictor_agent.feedback import PredictionFeedbackInput, PredictionFeedbackService


def _database_url(tmp_path) -> str:
    return f"sqlite:///{tmp_path / 'experience-bank.db'}"


def _prediction(session, *, predicted: float, outbreak: bool) -> Prediction:
    row = Prediction(
        country="United States",
        admin1=None,
        disease="norovirus",
        target_date=date(2025, 3, 1),
        iso_week="2025-W08",
        horizon_days=7,
        model_name="feature_rule",
        model_version="v1",
        predicted_value=predicted,
        lower_bound=max(0.0, predicted - 2),
        upper_bound=predicted + 2,
        uncertainty_method="rolling_residual_std_95pct",
        prediction_interval_json={"level": 0.95},
        metrics_json={},
        provenance_json={"predicted_outbreak_label": outbreak},
        review_status="pending",
    )
    session.add(row)
    session.flush()
    return row


def test_experience_bank_failure_reasons_and_actions(tmp_path) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()

    with get_session(database_url) as session:
        service = PredictionFeedbackService(session)
        cases = [
            (_prediction(session, predicted=5.0, outbreak=False), 12.0, True, "missed_outbreak", "review_outbreak_signal_features"),
            (_prediction(session, predicted=20.0, outbreak=True), 8.0, False, "false_alarm", "review_false_alarm_thresholds"),
            (_prediction(session, predicted=15.0, outbreak=True), 9.0, True, "over_prediction", "review_over_prediction_features"),
        ]
        for prediction, actual_value, actual_label, reason, _action in cases:
            service.submit_feedback(
                PredictionFeedbackInput(
                    prediction_id=prediction.id,
                    feedback_status="failure",
                    actual_value=actual_value,
                    actual_outbreak_label=actual_label,
                    failure_reason=reason,
                )
            )
        experiences = session.query(ModelExperienceBank).order_by(ModelExperienceBank.id).all()

    assert [experience.failure_reason for experience in experiences] == [
        "missed_outbreak",
        "false_alarm",
        "over_prediction",
    ]
    assert [experience.recommended_action for experience in experiences] == [
        "review_outbreak_signal_features",
        "review_false_alarm_thresholds",
        "review_over_prediction_features",
    ]
    assert experiences[0].classification_correct is False
    assert experiences[1].classification_correct is False
    assert experiences[2].classification_correct is True
    assert all(experience.experience_json["auto_retrain_triggered"] is False for experience in experiences)


def test_experience_bank_service_creates_skips_success_and_deduplicates(tmp_path) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()

    with get_session(database_url) as session:
        failed_prediction = _prediction(session, predicted=3.0, outbreak=False)
        success_prediction = _prediction(session, predicted=10.0, outbreak=True)
        failure = HumanFeedback(
            prediction_id=failed_prediction.id,
            feedback_status="failure",
            actual_value=12.0,
            actual_outbreak_label=True,
            failure_reason=None,
            reviewer_ref="anonymous",
            metadata_json={},
        )
        success = HumanFeedback(
            prediction_id=success_prediction.id,
            feedback_status="success",
            actual_value=10.0,
            actual_outbreak_label=True,
            reviewer_ref="anonymous",
            metadata_json={},
        )
        session.add_all([failure, success])
        session.flush()
        service = ExperienceBankService(session)
        created = service.create_experience_from_feedback(failure.id)
        duplicate = service.create_experience_from_feedback(failure.id)
        skipped = service.create_experience_from_feedback(success.id)
        summary = service.summarize_experiences(country="United States", disease="norovirus")
        listed = service.list_experiences(failure_type="missed_outbreak")

    assert created is duplicate
    assert skipped is None
    assert created is not None
    assert created.failure_type == "missed_outbreak"
    assert created.lesson_json["true_cases"] == 12.0
    assert created.weight_for_retraining is not None
    assert len(listed) == 1
    assert summary["total"] == 1
    assert summary["by_failure_type"] == {"missed_outbreak": 1}
    assert summary["average_weight"] > 0
