from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.api.deps import get_db_session
from apps.api.safety import sanitize_mapping
from apps.api.schemas import JobResponse, ModelBacktestRequest, ModelPredictRequest
from common.models import Prediction
from predictor_agent.agent import PredictorAgent

router = APIRouter(tags=["models"])


@router.post("/models/backtest", response_model=JobResponse)
def model_backtest(request: ModelBacktestRequest, session: Session = Depends(get_db_session)) -> JobResponse:
    result = PredictorAgent(session).backtest(
        country=request.country,
        disease=request.disease,
        admin1=request.admin1,
        horizon_days=request.horizon_days,
        model_name=request.model_name,
        min_train_size=request.min_train_size,
        test_window=request.test_window,
    )
    metrics = sanitize_mapping(result.get("metrics", {}), max_items=20)
    if isinstance(metrics, dict):
        metrics["model_name"] = result.get("model_name")
    preview = [
        {
            "feature_record_id": row.get("feature_record_id"),
            "target_date": row.get("target_date"),
            "actual_cases": row.get("actual_cases"),
            "predicted_cases": row.get("predicted_cases"),
            "actual_outbreak": row.get("actual_outbreak"),
            "predicted_outbreak_probability": row.get("predicted_outbreak_probability"),
            "lower_bound": row.get("lower_bound"),
            "upper_bound": row.get("upper_bound"),
        }
        for row in result.get("predictions", [])[:20]
    ]
    return JobResponse(
        status="ok",
        summary={
            "model_name": result.get("model_name"),
            "horizon_days": result.get("horizon_days"),
            "features_seen": result.get("features_seen", 0),
            "backtest_records": len(result.get("predictions", [])),
            "splits": result.get("splits", 0),
            "metrics": metrics,
            "preview": preview,
            "limitations": "Backtest metrics reflect current historical data and settings only.",
        },
        warnings=list(result.get("warnings", [])),
    )


@router.post("/models/predict", response_model=JobResponse)
def model_predict(request: ModelPredictRequest, session: Session = Depends(get_db_session)) -> JobResponse:
    result = PredictorAgent(session).predict_latest(
        country=request.country,
        disease=request.disease,
        admin1=request.admin1,
        horizon_days=request.horizon_days,
        model_name=request.model_name,
        min_train_size=request.min_train_size,
        replace_existing=True,
        write_predictions=request.write_predictions,
    )
    safe_summary = {
        key: value
        for key, value in result.items()
        if key
        in {
            "model_name",
            "model_version",
            "horizon_days",
            "features_seen",
            "prediction_rows",
            "predictions_written",
            "saved",
            "updated",
            "skipped",
        }
    }
    safe_summary["predictions"] = sanitize_mapping(result.get("predictions", [])[: request.limit], max_items=20)
    safe_summary["limitations"] = "Predictions include uncertainty and require human review; accuracy is not guaranteed."
    return JobResponse(status="ok", summary=safe_summary, warnings=list(result.get("warnings", [])))


@router.get("/metrics/backtest")
def metrics_backtest(
    country: str | None = None,
    disease: str | None = None,
    admin1: str | None = None,
    horizon_days: int = Query(default=28, ge=1, le=365),
    model_name: str = "moving_average",
    min_train_size: int = Query(default=8, ge=1, le=10000),
    test_window: int = Query(default=1, ge=1, le=52),
    session: Session = Depends(get_db_session),
) -> dict:
    result = PredictorAgent(session).backtest(
        country=country,
        disease=disease,
        admin1=admin1,
        horizon_days=horizon_days,
        model_name=model_name,
        min_train_size=min_train_size,
        test_window=test_window,
    )
    return {
        "model_name": result.get("model_name"),
        "horizon_days": result.get("horizon_days"),
        "features_seen": result.get("features_seen", 0),
        "backtest_records": len(result.get("predictions", [])),
        "metrics": sanitize_mapping(result.get("metrics", {}), max_items=20),
        "warnings": list(result.get("warnings", [])),
        "limitations": "Offline backtest summary only; future accuracy is not guaranteed and analyst review is required.",
    }


@router.get("/predictions")
def list_predictions(
    country: str | None = None,
    disease: str | None = None,
    horizon_days: int | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    session: Session = Depends(get_db_session),
) -> dict:
    statement = select(Prediction).order_by(Prediction.id).limit(limit)
    if country:
        statement = statement.where(Prediction.country == country)
    if disease:
        statement = statement.where(Prediction.disease == disease)
    if horizon_days is not None:
        statement = statement.where(Prediction.horizon_days == horizon_days)
    rows = session.scalars(statement).all()
    return {"predictions": [_prediction_summary(row) for row in rows], "count": len(rows)}


def _prediction_summary(prediction: Prediction) -> dict:
    metrics = prediction.metrics_json if isinstance(prediction.metrics_json, dict) else {}
    provenance = prediction.provenance_json if isinstance(prediction.provenance_json, dict) else {}
    return {
        "id": prediction.id,
        "country": prediction.country,
        "admin1": prediction.admin1,
        "disease": prediction.disease,
        "target_date": prediction.target_date.isoformat(),
        "iso_week": prediction.iso_week,
        "horizon_days": prediction.horizon_days,
        "model_name": prediction.model_name,
        "model_version": prediction.model_version,
        "predicted_value": prediction.predicted_value,
        "predicted_cases": prediction.predicted_value,
        "lower_bound": prediction.lower_bound,
        "upper_bound": prediction.upper_bound,
        "uncertainty_method": prediction.uncertainty_method,
        "predicted_outbreak_probability": metrics.get("predicted_outbreak_probability")
        or provenance.get("predicted_outbreak_probability"),
        "risk_level": metrics.get("risk_level") or provenance.get("risk_level"),
        "prediction_interval_level": (prediction.prediction_interval_json or {}).get("level"),
        "metrics_keys": sorted(str(key) for key in metrics.keys())[:20],
        "provenance_keys": sorted(str(key) for key in provenance.keys())[:20],
        "review_status": prediction.review_status,
        "limitations": "Baseline prediction with uncertainty; analyst review is required.",
    }
