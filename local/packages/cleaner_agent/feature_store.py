from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from common.models import ModelFeature, utc_now


class ModelFeatureStore:
    def __init__(self, session: Session):
        self.session = session

    def save_features(self, rows: list[dict], *, replace_existing: bool = True) -> dict:
        summary = {
            "rows_seen": len(rows),
            "saved": 0,
            "updated": 0,
            "skipped": 0,
        }
        for row in rows:
            existing = self._find_existing(row)
            if existing is not None:
                if not replace_existing:
                    summary["skipped"] += 1
                    continue
                self._apply_row(existing, row)
                existing.updated_at = utc_now()
                summary["updated"] += 1
                continue
            self.session.add(self._create_feature(row))
            summary["saved"] += 1
        self.session.flush()
        return summary

    def count_features(self, country: str | None = None, disease: str | None = None) -> int:
        statement = select(ModelFeature)
        if country is not None:
            statement = statement.where(ModelFeature.country == country)
        if disease is not None:
            statement = statement.where(ModelFeature.disease == disease)
        return len(self.session.scalars(statement).all())

    def _find_existing(self, row: dict) -> ModelFeature | None:
        statement = select(ModelFeature).where(
            ModelFeature.country == row["country"],
            ModelFeature.disease == row["disease"],
            ModelFeature.iso_week == row["iso_week"],
            ModelFeature.horizon_days == int(row["horizon_days"]),
        )
        admin1 = row.get("admin1")
        if admin1 is None:
            statement = statement.where(ModelFeature.admin1.is_(None))
        else:
            statement = statement.where(ModelFeature.admin1 == admin1)
        return self.session.scalar(statement)

    def _create_feature(self, row: dict) -> ModelFeature:
        feature = ModelFeature(
            country=row["country"],
            admin1=row.get("admin1"),
            disease=row["disease"],
            target_date=_as_date(row["target_date"]),
            iso_week=row["iso_week"],
            horizon_days=int(row["horizon_days"]),
            split_type=row.get("split_type", "train"),
            feature_json={},
            target_value=0.0,
            target_outbreak_label=False,
        )
        self._apply_row(feature, row)
        return feature

    def _apply_row(self, feature: ModelFeature, row: dict) -> None:
        feature.target_date = _as_date(row["target_date"])
        feature.feature_json = _json_safe_dict(row.get("feature_json") or {})
        feature.target_value = float(row.get("target_value") or 0.0)
        feature.target_outbreak_label = bool(row.get("target_outbreak_label"))
        feature.split_type = str(row.get("split_type") or "train")


def _as_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value)
    raise ValueError("target_date must be a date or ISO date string.")


def _json_safe_dict(value: dict) -> dict:
    safe: dict[str, Any] = {}
    for key, item in value.items():
        if isinstance(item, (str, int, float, bool)) or item is None:
            safe[str(key)] = item
        elif isinstance(item, date):
            safe[str(key)] = item.isoformat()
        else:
            safe[str(key)] = str(item)
    return safe
