from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from common.models import ModelFeature


@dataclass(frozen=True)
class FeatureRow:
    id: int | None
    country: str
    admin1: str | None
    disease: str
    target_date: date
    iso_week: str
    horizon_days: int
    feature_json: dict
    target_value: float
    target_outbreak_label: bool
    split_type: str


def row_from_model_feature(feature: ModelFeature) -> FeatureRow:
    return FeatureRow(
        id=feature.id,
        country=feature.country,
        admin1=feature.admin1,
        disease=feature.disease,
        target_date=feature.target_date,
        iso_week=feature.iso_week,
        horizon_days=feature.horizon_days,
        feature_json=feature.feature_json or {},
        target_value=float(feature.target_value),
        target_outbreak_label=bool(feature.target_outbreak_label),
        split_type=feature.split_type,
    )


def load_feature_rows(
    session: Session,
    *,
    country: str | None = None,
    disease: str | None = None,
    admin1: str | None = None,
    split_type: str | None = None,
    horizons_days: tuple[int, ...] | None = None,
) -> list[FeatureRow]:
    statement = select(ModelFeature).order_by(
        ModelFeature.country,
        ModelFeature.admin1,
        ModelFeature.disease,
        ModelFeature.horizon_days,
        ModelFeature.target_date,
        ModelFeature.iso_week,
    )
    if country is not None:
        statement = statement.where(ModelFeature.country == country)
    if disease is not None:
        statement = statement.where(ModelFeature.disease == disease)
    if admin1 is not None:
        statement = statement.where(ModelFeature.admin1 == admin1)
    if split_type is not None:
        statement = statement.where(ModelFeature.split_type == split_type)
    if horizons_days:
        statement = statement.where(ModelFeature.horizon_days.in_(list(horizons_days)))
    return [row_from_model_feature(feature) for feature in session.scalars(statement).all()]
