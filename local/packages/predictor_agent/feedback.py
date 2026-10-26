from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from common.models import HumanFeedback, ModelExperienceBank, Prediction, PredictionFeedback, utc_now
from predictor_agent.experience_bank import ExperienceBankService
from predictor_agent.failure_analysis import (
    assign_retraining_weight as _assign_retraining_weight,
    build_failure_lesson,
    classify_failure_type as _classify_failure_type,
)

VALID_FEEDBACK_STATUSES = {"success", "partial_success", "failure", "unknown"}
ALLOWED_VERDICTS = {"success", "partial", "fail", "unknown"}
VALID_FAILURE_REASONS = {
    "under_prediction",
    "over_prediction",
    "missed_outbreak",
    "false_alarm",
    "data_gap",
    "source_quality",
    "timing_error",
    "unknown",
}


@dataclass(frozen=True, init=False)
class PredictionFeedbackInput:
    prediction_id: int | str
    reviewer: str | None
    verdict: str
    true_cases: float | None
    true_outbreak_label: bool | None
    reason: str | None
    notes: str | None
    feedback_status: str
    actual_value: float | None = None
    actual_outbreak_label: bool | None = None
    failure_reason: str | None = None
    reviewer_ref: str | None = None
    metadata_json: dict[str, Any] | None = None

    def __init__(
        self,
        prediction_id: int | str,
        reviewer: str | None = None,
        verdict: str | None = None,
        true_cases: float | None = None,
        true_outbreak_label: bool | None = None,
        reason: str | None = None,
        notes: str | None = None,
        *,
        feedback_status: str | None = None,
        actual_value: float | None = None,
        actual_outbreak_label: bool | None = None,
        failure_reason: str | None = None,
        reviewer_ref: str | None = None,
        metadata_json: dict[str, Any] | None = None,
    ) -> None:
        canonical_verdict = normalize_verdict(feedback_status if feedback_status is not None else verdict)
        internal_status = _verdict_to_status(canonical_verdict)
        resolved_true_cases = actual_value if actual_value is not None else true_cases
        resolved_true_label = (
            actual_outbreak_label if actual_outbreak_label is not None else true_outbreak_label
        )
        resolved_reason = failure_reason if failure_reason is not None else reason
        resolved_reviewer = reviewer_ref if reviewer_ref is not None else reviewer
        object.__setattr__(self, "prediction_id", prediction_id)
        object.__setattr__(self, "reviewer", resolved_reviewer)
        object.__setattr__(self, "verdict", canonical_verdict)
        object.__setattr__(self, "true_cases", resolved_true_cases)
        object.__setattr__(self, "true_outbreak_label", resolved_true_label)
        object.__setattr__(self, "reason", resolved_reason)
        object.__setattr__(self, "notes", notes)
        object.__setattr__(self, "feedback_status", internal_status)
        object.__setattr__(self, "actual_value", resolved_true_cases)
        object.__setattr__(self, "actual_outbreak_label", resolved_true_label)
        object.__setattr__(self, "failure_reason", resolved_reason)
        object.__setattr__(self, "reviewer_ref", resolved_reviewer)
        object.__setattr__(self, "metadata_json", metadata_json)


FeedbackInput = PredictionFeedbackInput


def normalize_verdict(verdict: str | None) -> str:
    normalized = (verdict or "unknown").strip().lower()
    aliases = {
        "partial_success": "partial",
        "failure": "fail",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in ALLOWED_VERDICTS:
        raise ValueError(f"Invalid verdict: {verdict}.")
    return normalized


def validate_feedback_input(feedback: FeedbackInput) -> list[str]:
    errors: list[str] = []
    if feedback.prediction_id is None or str(feedback.prediction_id).strip() == "":
        errors.append("prediction_id is required.")
    try:
        normalize_verdict(feedback.verdict)
    except ValueError:
        errors.append("verdict is invalid.")
    try:
        true_cases = _optional_float(feedback.true_cases)
        if true_cases is not None and true_cases < 0:
            errors.append("true_cases must be non-negative.")
    except (TypeError, ValueError):
        errors.append("true_cases must be numeric when provided.")
    return errors


def classify_failure_type(
    predicted_value: float | None = None,
    actual_value: float | None = None,
    predicted_outbreak_label: bool | None = None,
    actual_outbreak_label: bool | None = None,
    explicit_reason: str | None = None,
    *,
    feedback: Any | None = None,
    prediction: Any | None = None,
    true_cases: float | None = None,
    true_outbreak_label: bool | None = None,
) -> str:
    if feedback is not None and prediction is not None:
        return _classify_failure_type(
            feedback=feedback,
            prediction=prediction,
            true_cases=true_cases,
            true_outbreak_label=true_outbreak_label,
        )
    explicit = _validate_failure_reason(explicit_reason)
    if explicit and explicit != "unknown":
        return explicit
    if predicted_outbreak_label is not None and actual_outbreak_label is not None:
        if not bool(predicted_outbreak_label) and bool(actual_outbreak_label):
            return "missed_outbreak"
        if bool(predicted_outbreak_label) and not bool(actual_outbreak_label):
            return "false_alarm"
    if predicted_value is None or actual_value is None:
        return "unknown"
    predicted = float(predicted_value)
    actual = float(actual_value)
    if actual > predicted:
        return "under_prediction"
    if actual < predicted:
        return "over_prediction"
    return "unknown"


def assign_retraining_weight(
    feedback_status: str | None = None,
    failure_reason: str | None = None,
    absolute_error: float | None = None,
    classification_correct: bool | None = None,
    *,
    failure_type: str | None = None,
    verdict: str | None = None,
) -> float:
    if verdict is not None or failure_type is not None:
        return _assign_retraining_weight(
            failure_type=failure_type or "unknown",
            verdict=verdict or feedback_status or "unknown",
            absolute_error=absolute_error,
        )
    if feedback_status is None:
        feedback_status = "unknown"
    status = _validate_status(feedback_status)
    reason = _validate_failure_reason(failure_reason)
    weight = {
        "success": 0.25,
        "partial_success": 0.75,
        "failure": 1.0,
        "unknown": 0.1,
    }[status]
    if reason in {"missed_outbreak", "false_alarm"}:
        weight = max(weight, 1.25)
    elif reason in {"data_gap", "source_quality"}:
        weight = max(weight, 0.8)
    if classification_correct is False:
        weight = max(weight, 1.0)
    if absolute_error is not None and float(absolute_error) >= 10.0:
        weight += 0.1
    return round(max(0.0, min(1.5, weight)), 4)


def build_lesson_json(
    prediction: Prediction,
    feedback: HumanFeedback,
    experience: ModelExperienceBank | None = None,
    failure_type: str | None = None,
) -> dict:
    signed_error = experience.signed_error if experience is not None else None
    absolute_error = experience.absolute_error if experience is not None else None
    predicted_label = experience.predicted_outbreak_label if experience is not None else _predicted_outbreak_label(
        prediction
    )
    classification_correct = experience.classification_correct if experience is not None else None
    if failure_type is None:
        failure_type = classify_failure_type(
            prediction.predicted_value,
            feedback.actual_value,
            predicted_label,
            feedback.actual_outbreak_label,
            feedback.failure_reason,
        )
    lesson_failure_reason = failure_type if failure_type != "unknown" else feedback.failure_reason
    return {
        "prediction_id": prediction.id,
        "feedback_id": feedback.id,
        "model_name": prediction.model_name,
        "model_version": prediction.model_version,
        "country": prediction.country,
        "admin1": prediction.admin1,
        "disease": prediction.disease,
        "target_date": prediction.target_date.isoformat(),
        "iso_week": prediction.iso_week,
        "horizon_days": prediction.horizon_days,
        "feedback_status": feedback.feedback_status,
        "failure_type": failure_type,
        "failure_reason": lesson_failure_reason,
        "signed_error": signed_error,
        "absolute_error": absolute_error,
        "predicted_outbreak_label": predicted_label,
        "actual_outbreak_label": feedback.actual_outbreak_label,
        "classification_correct": classification_correct,
        "retraining_weight": assign_retraining_weight(
            feedback.feedback_status,
            lesson_failure_reason,
            absolute_error,
            classification_correct,
        ),
        "recommended_action": _recommended_action(lesson_failure_reason, feedback.feedback_status),
        "review_required": True,
        "auto_retrain_triggered": False,
        "auto_model_replacement_triggered": False,
    }


class PredictionFeedbackService:
    def __init__(self, session: Session):
        self.session = session

    def submit_feedback(self, feedback_input: PredictionFeedbackInput) -> dict:
        validation_errors = validate_feedback_input(feedback_input)
        if validation_errors:
            raise ValueError("; ".join(validation_errors))
        status = _validate_status(feedback_input.feedback_status)
        failure_reason = _validate_failure_reason(feedback_input.failure_reason)
        prediction = self.session.get(Prediction, int(feedback_input.prediction_id))
        if prediction is None:
            raise ValueError("prediction_id must identify an existing prediction.")

        existing_feedback = self.session.scalar(
            select(HumanFeedback).where(
                HumanFeedback.prediction_id == prediction.id,
            )
        )
        created = existing_feedback is None
        feedback = existing_feedback or HumanFeedback(
            prediction_id=prediction.id,
            feedback_status=status,
            metadata_json={},
        )
        feedback.feedback_status = status
        feedback.actual_value = _optional_float(feedback_input.actual_value)
        feedback.actual_outbreak_label = feedback_input.actual_outbreak_label
        feedback.failure_reason = failure_reason
        feedback.notes = _sanitize_free_text(feedback_input.notes, max_length=2000)
        feedback.reviewer_ref = _sanitize_free_text(feedback_input.reviewer_ref, max_length=160) or "anonymous"
        feedback.metadata_json = dict(feedback_input.metadata_json or {})
        feedback.updated_at = utc_now()
        if created:
            self.session.add(feedback)
        prediction.review_status = "reviewed"
        self.session.flush()

        experience, experience_created = self._create_experience_if_needed(prediction, feedback)
        self.session.flush()
        return {
            "prediction_id": prediction.id,
            "feedback_id": feedback.id,
            "experience_id": experience.id if experience is not None else None,
            "status": feedback.feedback_status,
            "verdict": feedback.verdict,
            "feedback_created": created,
            "experience_created": experience_created,
        }

    def get_feedback_for_prediction(self, prediction_id: str | int) -> list[HumanFeedback]:
        return list(
            self.session.scalars(
                select(HumanFeedback)
                .where(HumanFeedback.prediction_id == int(prediction_id))
                .order_by(HumanFeedback.id)
            ).all()
        )

    def summarize_feedback(
        self,
        *,
        country: str | None = None,
        disease: str | None = None,
    ) -> dict:
        statement = select(HumanFeedback).join(Prediction)
        if country:
            statement = statement.where(Prediction.country == country)
        if disease:
            statement = statement.where(Prediction.disease == disease)
        feedback_rows = self.session.scalars(statement).all()
        by_status = {status: 0 for status in sorted(VALID_FEEDBACK_STATUSES)}
        by_verdict = {verdict: 0 for verdict in sorted(ALLOWED_VERDICTS)}
        failure_reasons: dict[str, int] = {}
        errors: list[float] = []
        for feedback in feedback_rows:
            by_status[feedback.feedback_status] = by_status.get(feedback.feedback_status, 0) + 1
            by_verdict[feedback.verdict] = by_verdict.get(feedback.verdict, 0) + 1
            if feedback.failure_reason:
                failure_reasons[feedback.failure_reason] = failure_reasons.get(feedback.failure_reason, 0) + 1
            if feedback.actual_value is not None and feedback.prediction is not None:
                errors.append(abs(float(feedback.actual_value) - float(feedback.prediction.predicted_value)))
        total = len(feedback_rows)
        failure_count = by_verdict.get("fail", 0)
        return {
            "total": total,
            "success": by_verdict.get("success", 0),
            "partial": by_verdict.get("partial", 0),
            "fail": failure_count,
            "unknown": by_verdict.get("unknown", 0),
            "failure_rate": failure_count / total if total else 0.0,
            "feedback_count": len(feedback_rows),
            "by_status": by_status,
            "by_verdict": by_verdict,
            "mean_absolute_error": sum(errors) / len(errors) if errors else 0.0,
            "failure_reasons": dict(sorted(failure_reasons.items())),
        }

    def _create_experience_if_needed(
        self,
        prediction: Prediction,
        feedback: HumanFeedback,
    ) -> tuple[ModelExperienceBank | None, bool]:
        if feedback.verdict not in {"fail", "partial"}:
            return None, False
        existing = self.session.scalar(
            select(ModelExperienceBank).where(ModelExperienceBank.feedback_id == feedback.id)
        )
        if existing is not None:
            return existing, False
        before = self.session.scalar(
            select(ModelExperienceBank).where(ModelExperienceBank.prediction_id == prediction.id)
        )
        if before is not None:
            return before, False
        experience = ExperienceBankService(self.session).create_experience_from_feedback(feedback.id)
        return experience, experience is not None

    def _upsert_experience(
        self,
        prediction: Prediction,
        feedback: HumanFeedback,
    ) -> tuple[ModelExperienceBank, bool]:
        existing = self.session.scalar(
            select(ModelExperienceBank).where(ModelExperienceBank.prediction_id == prediction.id)
        )
        created = existing is None
        experience = existing or ModelExperienceBank(
            prediction_id=prediction.id,
            feedback_id=feedback.id,
            country=prediction.country,
            disease=prediction.disease,
            target_date=prediction.target_date,
            iso_week=prediction.iso_week,
            horizon_days=prediction.horizon_days,
            model_name=prediction.model_name,
            model_version=prediction.model_version,
            predicted_value=prediction.predicted_value,
            feedback_status=feedback.feedback_status,
            experience_json={},
            recommended_action="manual_review",
        )
        actual_value = feedback.actual_value
        signed_error = absolute_error = None
        if actual_value is not None:
            signed_error = actual_value - prediction.predicted_value
            absolute_error = abs(signed_error)
        predicted_label = _predicted_outbreak_label(prediction)
        classification_correct = None
        if predicted_label is not None and feedback.actual_outbreak_label is not None:
            classification_correct = predicted_label == feedback.actual_outbreak_label
        experience.feedback_id = feedback.id
        experience.country = prediction.country
        experience.admin1 = prediction.admin1
        experience.disease = prediction.disease
        experience.target_date = prediction.target_date
        experience.iso_week = prediction.iso_week
        experience.horizon_days = prediction.horizon_days
        experience.model_name = prediction.model_name
        experience.model_version = prediction.model_version
        experience.predicted_value = prediction.predicted_value
        experience.actual_value = actual_value
        experience.absolute_error = absolute_error
        experience.signed_error = signed_error
        experience.predicted_outbreak_label = predicted_label
        experience.actual_outbreak_label = feedback.actual_outbreak_label
        experience.classification_correct = classification_correct
        classified_failure = classify_failure_type(
            prediction.predicted_value,
            actual_value,
            predicted_label,
            feedback.actual_outbreak_label,
            feedback.failure_reason,
        )
        inferred_failure_reason = classified_failure if classified_failure != "unknown" else feedback.failure_reason
        if feedback.feedback_status not in {"failure", "partial_success"} and not feedback.failure_reason:
            inferred_failure_reason = None
        experience.feedback_status = feedback.feedback_status
        experience.failure_reason = inferred_failure_reason
        experience.experience_json = build_lesson_json(prediction, feedback, experience, classified_failure)
        experience.recommended_action = _recommended_action(inferred_failure_reason, feedback.feedback_status)
        experience.updated_at = utc_now()
        if created:
            self.session.add(experience)
        return experience, created


class FeedbackService(PredictionFeedbackService):
    pass


class FeedbackStore(FeedbackService):
    def add_feedback(self, feedback_input: FeedbackInput) -> dict:
        return self.submit_feedback(feedback_input)


class ExperienceBankStore(ExperienceBankService):
    def __init__(self, session: Session):
        self.session = session

    def get_for_prediction(self, prediction_id: int) -> ModelExperienceBank | None:
        return self.session.scalar(
            select(ModelExperienceBank).where(ModelExperienceBank.prediction_id == int(prediction_id))
        )

    def get_for_feedback(self, feedback_id: int) -> ModelExperienceBank | None:
        return self.session.scalar(
            select(ModelExperienceBank).where(ModelExperienceBank.feedback_id == int(feedback_id))
        )

    def list_experiences(
        self,
        *,
        model_name: str | None = None,
        disease: str | None = None,
        country: str | None = None,
        failure_reason: str | None = None,
        failure_type: str | None = None,
        limit: int | None = 100,
    ) -> list[ModelExperienceBank]:
        statement = select(ModelExperienceBank).order_by(ModelExperienceBank.id)
        if model_name:
            statement = statement.where(ModelExperienceBank.model_name == model_name)
        if disease:
            statement = statement.where(ModelExperienceBank.disease == disease)
        if country:
            statement = statement.where(ModelExperienceBank.country == country)
        resolved_failure = failure_type or failure_reason
        if resolved_failure:
            statement = statement.where(ModelExperienceBank.failure_reason == resolved_failure)
        if limit is not None:
            statement = statement.limit(max(0, int(limit)))
        return list(self.session.scalars(statement).all())

    def summarize(self) -> dict:
        experiences = self.list_experiences(limit=None)
        by_failure_type: dict[str, int] = {}
        by_model: dict[str, int] = {}
        errors: list[float] = []
        weights: list[float] = []
        for experience in experiences:
            payload = experience.experience_json if isinstance(experience.experience_json, dict) else {}
            failure_type = payload.get("failure_type") or experience.failure_reason or "unknown"
            by_failure_type[failure_type] = by_failure_type.get(failure_type, 0) + 1
            by_model[experience.model_name] = by_model.get(experience.model_name, 0) + 1
            if experience.absolute_error is not None:
                errors.append(float(experience.absolute_error))
            retraining_weight = payload.get("retraining_weight")
            if retraining_weight is not None:
                weights.append(float(retraining_weight))
        return {
            "total": len(experiences),
            "experience_count": len(experiences),
            "by_failure_type": dict(sorted(by_failure_type.items())),
            "by_model": dict(sorted(by_model.items())),
            "average_weight": sum(weights) / len(weights) if weights else 0.0,
            "mean_absolute_error": sum(errors) / len(errors) if errors else 0.0,
            "mean_retraining_weight": sum(weights) / len(weights) if weights else 0.0,
        }

    def summarize_experiences(self) -> dict:
        return self.summarize()


def _validate_status(value: str) -> str:
    return _verdict_to_status(normalize_verdict(value))


def _validate_failure_reason(value: str | None) -> str | None:
    if value is None or not str(value).strip():
        return None
    normalized = _sanitize_free_text(str(value).strip().lower(), max_length=80)
    return normalized or None


def _verdict_to_status(verdict: str) -> str:
    if verdict == "fail":
        return "failure"
    if verdict == "partial":
        return "partial_success"
    return verdict


def _sanitize_free_text(value: str | None, *, max_length: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    lowered = text.lower()
    sensitive_terms = ("secret", "token", "password", "passwd", "credential", "private key", "ssh key")
    if any(term in lowered for term in sensitive_terms):
        return "***"
    return text[:max_length]


def _optional_float(value: float | int | str | None) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _predicted_outbreak_label(prediction: Prediction) -> bool | None:
    provenance = prediction.provenance_json or {}
    if isinstance(provenance, dict) and "predicted_outbreak_label" in provenance:
        return bool(provenance["predicted_outbreak_label"])
    metrics = prediction.metrics_json or {}
    if isinstance(metrics, dict) and "predicted_outbreak_label" in metrics:
        return bool(metrics["predicted_outbreak_label"])
    return None


def _experience_payload(
    prediction: Prediction,
    feedback: HumanFeedback,
    signed_error: float | None,
    absolute_error: float | None,
) -> dict:
    predicted_label = _predicted_outbreak_label(prediction)
    failure_type = classify_failure_type(
        prediction.predicted_value,
        feedback.actual_value,
        predicted_label,
        feedback.actual_outbreak_label,
        feedback.failure_reason,
    )
    payload = build_lesson_json(prediction, feedback, None, failure_type)
    payload["signed_error"] = signed_error
    payload["absolute_error"] = absolute_error
    payload["retraining_weight"] = assign_retraining_weight(
        feedback.feedback_status,
        payload.get("failure_reason"),
        absolute_error,
        payload.get("classification_correct"),
    )
    return payload


def _recommended_action(failure_reason: str | None, status: str) -> str:
    if status == "unknown":
        return "collect_more_ground_truth"
    if failure_reason == "under_prediction":
        return "review_under_prediction_features"
    if failure_reason == "over_prediction":
        return "review_over_prediction_features"
    if failure_reason == "missed_outbreak":
        return "review_outbreak_signal_features"
    if failure_reason == "false_alarm":
        return "review_false_alarm_thresholds"
    if failure_reason == "data_gap":
        return "improve_data_coverage"
    if failure_reason == "source_quality":
        return "review_source_quality"
    if failure_reason == "timing_error":
        return "review_temporal_alignment"
    return "manual_review"
