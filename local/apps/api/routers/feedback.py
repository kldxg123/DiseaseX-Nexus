from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.api.deps import get_db_session
from apps.api.safety import notes_summary, sanitize_mapping
from apps.api.schemas import ExperienceBuildRequest, FeedbackCreateRequest, JobResponse
from common.models import HumanFeedback, ModelExperienceBank, Prediction
from predictor_agent.feedback import ExperienceBankStore, FeedbackInput, FeedbackStore
from predictor_agent.experience_bank import ExperienceBankService

router = APIRouter(tags=["feedback"])


@router.post("/feedback", response_model=JobResponse)
def create_feedback(request: FeedbackCreateRequest, session: Session = Depends(get_db_session)) -> JobResponse:
    try:
        prediction_id = int(request.prediction_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("prediction_id must identify an existing prediction.") from exc
    result = FeedbackStore(session).add_feedback(
        FeedbackInput(
            prediction_id=prediction_id,
            feedback_status=request.verdict,
            actual_value=request.true_cases,
            actual_outbreak_label=request.true_outbreak_label,
            failure_reason=request.reason,
            notes=request.notes,
            reviewer_ref=request.reviewer,
        )
    )
    return JobResponse(status="ok", summary={**result, "notes_present": bool(request.notes)})


@router.get("/feedback")
def list_feedback(
    verdict: str | None = None,
    country: str | None = None,
    disease: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    session: Session = Depends(get_db_session),
) -> dict:
    statement = select(HumanFeedback).join(Prediction).order_by(HumanFeedback.id).limit(limit)
    if verdict:
        statement = statement.where(HumanFeedback.feedback_status == verdict)
    if country:
        statement = statement.where(Prediction.country == country)
    if disease:
        statement = statement.where(Prediction.disease == disease)
    rows = session.scalars(statement).all()
    return {"feedback": [_feedback_summary(row) for row in rows], "count": len(rows)}


@router.get("/feedback/summary")
def feedback_summary(session: Session = Depends(get_db_session)) -> dict:
    return FeedbackStore(session).summarize_feedback()


@router.get("/experiences")
def list_experiences(
    country: str | None = None,
    disease: str | None = None,
    failure_type: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    session: Session = Depends(get_db_session),
) -> dict:
    statement = select(ModelExperienceBank).order_by(ModelExperienceBank.id).limit(limit)
    if country:
        statement = statement.where(ModelExperienceBank.country == country)
    if disease:
        statement = statement.where(ModelExperienceBank.disease == disease)
    if failure_type:
        statement = statement.where(ModelExperienceBank.failure_reason == failure_type)
    rows = session.scalars(statement).all()
    return {"experiences": [_experience_summary(row) for row in rows], "count": len(rows)}


@router.get("/experiences/summary")
def experiences_summary(session: Session = Depends(get_db_session)) -> dict:
    return ExperienceBankStore(session).summarize_experiences()


@router.post("/experience/build", response_model=JobResponse)
def build_experience(request: ExperienceBuildRequest, session: Session = Depends(get_db_session)) -> JobResponse:
    experience = ExperienceBankService(session).create_experience_from_feedback(request.feedback_id)
    return JobResponse(
        status="ok",
        summary={
            "feedback_id": int(request.feedback_id),
            "experience_id": experience.id if experience is not None else None,
            "experience_created_or_existing": experience is not None,
            "failure_reason": experience.failure_reason if experience is not None else None,
            "recommended_action": experience.recommended_action if experience is not None else None,
            "review_required": True,
            "auto_retrain_triggered": False,
            "auto_model_replacement_triggered": False,
        },
    )


@router.get("/experience/summary")
def experience_summary(session: Session = Depends(get_db_session)) -> dict:
    return ExperienceBankStore(session).summarize_experiences()


def _feedback_summary(row: HumanFeedback) -> dict:
    prediction = row.prediction
    return {
        "id": row.id,
        "prediction_id": row.prediction_id,
        "feedback_status": row.feedback_status,
        "actual_value": row.actual_value,
        "actual_outbreak_label": row.actual_outbreak_label,
        "failure_reason": row.failure_reason,
        "reviewer_ref": row.reviewer_ref,
        "metadata_json": sanitize_mapping(row.metadata_json or {}),
        **notes_summary(row.notes),
        "prediction": {
            "country": prediction.country,
            "disease": prediction.disease,
            "horizon_days": prediction.horizon_days,
            "model_name": prediction.model_name,
        }
        if prediction is not None
        else None,
    }


def _experience_summary(row: ModelExperienceBank) -> dict:
    payload = row.experience_json if isinstance(row.experience_json, dict) else {}
    return {
        "id": row.id,
        "prediction_id": row.prediction_id,
        "feedback_id": row.feedback_id,
        "country": row.country,
        "admin1": row.admin1,
        "disease": row.disease,
        "target_date": row.target_date.isoformat(),
        "iso_week": row.iso_week,
        "horizon_days": row.horizon_days,
        "model_name": row.model_name,
        "model_version": row.model_version,
        "predicted_value": row.predicted_value,
        "actual_value": row.actual_value,
        "absolute_error": row.absolute_error,
        "signed_error": row.signed_error,
        "classification_correct": row.classification_correct,
        "feedback_status": row.feedback_status,
        "failure_reason": row.failure_reason,
        "recommended_action": row.recommended_action,
        "lesson_summary": sanitize_mapping(
            {
                "failure_type": payload.get("failure_type"),
                "retraining_weight": payload.get("retraining_weight"),
                "review_required": payload.get("review_required"),
                "auto_retrain_triggered": payload.get("auto_retrain_triggered"),
                "auto_model_replacement_triggered": payload.get("auto_model_replacement_triggered"),
            }
        ),
    }
