from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import select

from common.models import ModelFeature
from predictor_agent.dataset import FeatureRow, load_feature_rows, row_from_model_feature


SeriesKey = tuple[str, str | None, str, int]


@dataclass(frozen=True)
class FeatureRecord:
    id: str
    country: str
    admin1: str | None
    disease: str
    target_date: date
    iso_week: str
    horizon_days: int
    feature_json: dict
    target_value: float | None
    target_outbreak_label: bool | None
    split_type: str | None


def model_feature_to_record(model_feature: Any) -> FeatureRecord:
    return FeatureRecord(
        id=str(getattr(model_feature, "id", "")),
        country=str(getattr(model_feature, "country")),
        admin1=getattr(model_feature, "admin1", None),
        disease=str(getattr(model_feature, "disease")),
        target_date=getattr(model_feature, "target_date"),
        iso_week=str(getattr(model_feature, "iso_week")),
        horizon_days=int(getattr(model_feature, "horizon_days")),
        feature_json=dict(getattr(model_feature, "feature_json", None) or {}),
        target_value=_optional_float(getattr(model_feature, "target_value", None)),
        target_outbreak_label=_optional_bool(getattr(model_feature, "target_outbreak_label", None)),
        split_type=getattr(model_feature, "split_type", None),
    )


def load_feature_records(
    session,
    *,
    country: str | None = None,
    disease: str | None = None,
    admin1: str | None = None,
    horizon_days: int | None = None,
    split_type: str | None = None,
) -> list[FeatureRecord]:
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
    if horizon_days is not None:
        statement = statement.where(ModelFeature.horizon_days == int(horizon_days))
    if split_type is not None:
        statement = statement.where(ModelFeature.split_type == split_type)
    return [model_feature_to_record(row) for row in session.scalars(statement).all()]


def sort_records_temporally(records: list[FeatureRecord]) -> list[FeatureRecord]:
    return sorted(records, key=lambda record: (record.country, record.admin1 or "", record.disease, record.horizon_days, record.target_date, record.iso_week))


def split_train_test_by_date(
    records: list[FeatureRecord],
    *,
    cutoff_date,
) -> tuple[list[FeatureRecord], list[FeatureRecord]]:
    ordered = sort_records_temporally(records)
    train = [record for record in ordered if record.target_date < cutoff_date]
    test = [record for record in ordered if record.target_date >= cutoff_date]
    return train, test


def group_rows_by_series(rows: list[FeatureRow]) -> dict[SeriesKey, list[FeatureRow]]:
    groups: dict[SeriesKey, list[FeatureRow]] = defaultdict(list)
    for row in rows:
        groups[(row.country, row.admin1, row.disease, row.horizon_days)].append(row)
    for key in groups:
        groups[key].sort(key=lambda item: (item.target_date, item.iso_week))
    return dict(groups)


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(value)


__all__ = [
    "FeatureRecord",
    "FeatureRow",
    "SeriesKey",
    "group_rows_by_series",
    "load_feature_records",
    "load_feature_rows",
    "model_feature_to_record",
    "row_from_model_feature",
    "sort_records_temporally",
    "split_train_test_by_date",
]
