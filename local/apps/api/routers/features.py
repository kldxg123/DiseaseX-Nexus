from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.api.deps import get_db_session
from apps.api.schemas import FeatureBuildRequest, JobResponse
from cleaner_agent.features import FeatureBuilder
from common.models import ModelFeature

router = APIRouter(tags=["features"])


@router.post("/features/build", response_model=JobResponse)
def build_features(request: FeatureBuildRequest, session: Session = Depends(get_db_session)) -> JobResponse:
    summary = FeatureBuilder(session).build(
        country=request.country,
        disease=request.disease,
        admin1=request.admin1,
        horizons_days=tuple(request.horizons_days),
        outbreak_threshold=request.outbreak_threshold,
        split_type=request.split_type,
        replace_existing=request.replace_existing,
    )
    return JobResponse(status="ok", summary=summary)


@router.get("/features")
def list_features(
    country: str | None = None,
    disease: str | None = None,
    horizon_days: int | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    session: Session = Depends(get_db_session),
) -> dict:
    statement = select(ModelFeature).order_by(ModelFeature.target_date, ModelFeature.id).limit(limit)
    if country:
        statement = statement.where(ModelFeature.country == country)
    if disease:
        statement = statement.where(ModelFeature.disease == disease)
    if horizon_days is not None:
        statement = statement.where(ModelFeature.horizon_days == horizon_days)
    rows = session.scalars(statement).all()
    return {"features": [_feature_summary(row) for row in rows], "count": len(rows)}


def _feature_summary(row: ModelFeature) -> dict:
    return {
        "id": row.id,
        "country": row.country,
        "admin1": row.admin1,
        "disease": row.disease,
        "target_date": row.target_date.isoformat(),
        "iso_week": row.iso_week,
        "horizon_days": row.horizon_days,
        "feature_keys": sorted(str(key) for key in (row.feature_json or {}).keys())[:40],
        "target_value": row.target_value,
        "target_outbreak_label": row.target_outbreak_label,
        "split_type": row.split_type,
    }
