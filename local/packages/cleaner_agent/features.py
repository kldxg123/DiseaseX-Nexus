from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from cleaner_agent.feature_engineering import (
    aggregate_events_weekly,
    build_features_from_weekly_aggregates,
)
from cleaner_agent.feature_store import ModelFeatureStore
from common.models import EpidemicEvent


class FeatureBuilder:
    def __init__(self, session: Session):
        self.session = session

    def build(
        self,
        *,
        country: str | None = None,
        disease: str | None = None,
        admin1: str | None = None,
        horizons_days: tuple[int, ...] = (7, 28),
        outbreak_threshold: int = 3,
        split_type: str = "train",
        replace_existing: bool = True,
    ) -> dict:
        statement = select(EpidemicEvent).order_by(EpidemicEvent.country, EpidemicEvent.disease, EpidemicEvent.iso_week)
        if country is not None:
            statement = statement.where(EpidemicEvent.country == country)
        if disease is not None:
            statement = statement.where(EpidemicEvent.disease == disease)
        if admin1 is not None:
            statement = statement.where(EpidemicEvent.admin1 == admin1)
        events = self.session.scalars(statement).all()
        weekly = aggregate_events_weekly(events, fill_missing_weeks=True)
        rows = build_features_from_weekly_aggregates(
            weekly,
            horizons_days=horizons_days,
            outbreak_threshold=outbreak_threshold,
            split_type=split_type,
        )
        store_summary = ModelFeatureStore(self.session).save_features(
            rows,
            replace_existing=replace_existing,
        )
        return {
            "events_seen": len(events),
            "weekly_rows": len(weekly),
            "feature_rows": len(rows),
            "saved": store_summary["saved"],
            "updated": store_summary["updated"],
            "skipped": store_summary["skipped"],
        }
