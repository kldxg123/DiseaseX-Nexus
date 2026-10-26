from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from common.models import HumanFeedback, ModelExperienceBank, Prediction, utc_now
from predictor_agent.failure_analysis import build_failure_lesson, classify_failure_type


class ExperienceBankService:
    def __init__(self, session: Session):
        self.session = session

    def create_experience_from_feedback(self, feedback_id: str | int) -> ModelExperienceBank | None:
        feedback = self.session.get(HumanFeedback, int(feedback_id))
        if feedback is None:
            raise ValueError("feedback_id must identify an existing feedback row.")
        if feedback.verdict not in {"fail", "partial"}:
            return None
        return self.create_experience_from_failed_prediction(feedback.prediction_id, feedback.id)

    def create_experience_from_failed_prediction(
        self,
        prediction_id: str | int,
        feedback_id: str | int,
    ) -> ModelExperienceBank:
        prediction = self.session.get(Prediction, int(prediction_id))
        feedback = self.session.get(HumanFeedback, int(feedback_id))
        if prediction is None:
            raise ValueError("prediction_id must identify an existing prediction.")
        if feedback is None:
            raise ValueError("feedback_id must identify an existing feedback row.")
        existing = self.session.scalar(
            select(ModelExperienceBank).where(ModelExperienceBank.feedback_id == feedback.id)
        )
        if existing is not None:
            return existing

        failure_type = classify_failure_type(
            feedback=feedback,
            prediction=prediction,
            true_cases=feedback.true_cases,
            true_outbreak_label=feedback.true_outbreak_label,
        )
        stored_failure_type = _stored_failure_type(feedback.failure_reason, failure_type)
        lesson = build_failure_lesson(
            feedback=feedback,
            prediction=prediction,
            failure_type=stored_failure_type,
        )
        absolute_error = lesson.get("absolute_error")
        signed_error = None
        if feedback.true_cases is not None:
            signed_error = float(feedback.true_cases) - float(prediction.predicted_value)
        experience = ModelExperienceBank(
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
            actual_value=feedback.true_cases,
            absolute_error=absolute_error,
            signed_error=signed_error,
            predicted_outbreak_label=_predicted_outbreak_label(prediction),
            actual_outbreak_label=feedback.true_outbreak_label,
            classification_correct=_classification_correct(prediction, feedback),
            feedback_status=feedback.feedback_status,
            failure_reason=stored_failure_type,
            experience_json=lesson,
            recommended_action=_recommended_action(stored_failure_type),
        )
        self.session.add(experience)
        self.session.flush()
        return experience

    def list_experiences(
        self,
        *,
        country: str | None = None,
        disease: str | None = None,
        failure_type: str | None = None,
        limit: int = 100,
    ) -> list[ModelExperienceBank]:
        statement = select(ModelExperienceBank).order_by(ModelExperienceBank.id)
        if country:
            statement = statement.where(ModelExperienceBank.country == country)
        if disease:
            statement = statement.where(ModelExperienceBank.disease == disease)
        if failure_type:
            statement = statement.where(ModelExperienceBank.failure_reason == failure_type)
        statement = statement.limit(max(0, int(limit)))
        return list(self.session.scalars(statement).all())

    def summarize_experiences(
        self,
        *,
        country: str | None = None,
        disease: str | None = None,
    ) -> dict:
        statement = select(ModelExperienceBank)
        if country:
            statement = statement.where(ModelExperienceBank.country == country)
        if disease:
            statement = statement.where(ModelExperienceBank.disease == disease)
        rows = list(self.session.scalars(statement).all())
        by_failure_type: dict[str, int] = {}
        by_model: dict[str, int] = {}
        weights: list[float] = []
        errors: list[float] = []
        for row in rows:
            failure_type = row.failure_type or "unknown"
            by_failure_type[failure_type] = by_failure_type.get(failure_type, 0) + 1
            by_model[row.model_name] = by_model.get(row.model_name, 0) + 1
            weight = row.weight_for_retraining
            if weight is not None:
                weights.append(weight)
            if row.absolute_error is not None:
                errors.append(float(row.absolute_error))
        average_weight = sum(weights) / len(weights) if weights else 0.0
        return {
            "total": len(rows),
            "experience_count": len(rows),
            "by_failure_type": dict(sorted(by_failure_type.items())),
            "by_model": dict(sorted(by_model.items())),
            "average_weight": average_weight,
            "mean_retraining_weight": average_weight,
            "mean_absolute_error": sum(errors) / len(errors) if errors else 0.0,
        }


def _predicted_outbreak_label(prediction: Prediction) -> bool | None:
    for payload in (prediction.provenance_json, prediction.metrics_json):
        if isinstance(payload, dict) and "predicted_outbreak_label" in payload:
            return bool(payload["predicted_outbreak_label"])
    probability = None
    if isinstance(prediction.metrics_json, dict):
        probability = prediction.metrics_json.get("predicted_outbreak_probability")
    if probability is not None:
        return float(probability) >= 0.5
    return None


def _classification_correct(prediction: Prediction, feedback: HumanFeedback) -> bool | None:
    predicted = _predicted_outbreak_label(prediction)
    if predicted is None or feedback.true_outbreak_label is None:
        return None
    return predicted == feedback.true_outbreak_label


def _recommended_action(failure_type: str) -> str:
    return {
        "late_signal": "review_signal_latency",
        "false_alarm": "review_false_alarm_thresholds",
        "missing_source": "review_source_coverage",
        "extraction_error": "review_extraction_rules",
        "model_error": "review_model_features",
        "label_delay": "review_label_timing",
        "under_prediction": "review_under_prediction_features",
        "over_prediction": "review_over_prediction_features",
        "missed_outbreak": "review_outbreak_signal_features",
        "data_gap": "improve_data_coverage",
        "source_quality": "review_source_quality",
        "timing_error": "review_temporal_alignment",
    }.get(failure_type, "manual_review")


def _stored_failure_type(explicit_reason: str | None, classified: str) -> str:
    if explicit_reason and explicit_reason in {
        "under_prediction",
        "over_prediction",
        "missed_outbreak",
        "false_alarm",
        "data_gap",
        "source_quality",
        "timing_error",
    }:
        return explicit_reason
    if classified == "late_signal":
        return "missed_outbreak"
    return classified
