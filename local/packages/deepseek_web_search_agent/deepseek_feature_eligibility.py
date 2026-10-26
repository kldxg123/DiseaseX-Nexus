from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from common.models import EpidemicEvent


@dataclass(frozen=True)
class DeepSeekFeatureEligibilityResult:
    promoted_event_count: int
    distinct_iso_weeks: int
    numeric_target_count: int
    disease_country_consistent: bool
    minimum_weeks_requirement: int
    feature_generation_allowed: bool
    blocker_types: list[str] = field(default_factory=list)
    recommended_next_action: str = "Collect more reviewed structured events before feature generation."

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def check_feature_eligibility_after_promotion(
    session: Session,
    *,
    event_ids: list[int] | None = None,
    minimum_weeks: int = 4,
) -> DeepSeekFeatureEligibilityResult:
    event_ids = [int(value) for value in (event_ids or []) if value is not None]
    if not event_ids:
        return DeepSeekFeatureEligibilityResult(
            promoted_event_count=0,
            distinct_iso_weeks=0,
            numeric_target_count=0,
            disease_country_consistent=True,
            minimum_weeks_requirement=minimum_weeks,
            feature_generation_allowed=False,
            blocker_types=["no_promoted_events"],
        )
    events = session.scalars(select(EpidemicEvent).where(EpidemicEvent.id.in_(event_ids))).all()
    iso_weeks = {event.iso_week for event in events if event.iso_week}
    numeric_count = sum(1 for event in events if _has_numeric_target(event))
    disease_country_pairs = {(event.disease, event.country) for event in events}
    blockers: list[str] = []
    if len(iso_weeks) < minimum_weeks:
        blockers.append("insufficient_distinct_iso_weeks")
    if numeric_count < len(events):
        blockers.append("missing_numeric_targets")
    if len(disease_country_pairs) > 1:
        blockers.append("mixed_disease_country")
    allowed = not blockers
    return DeepSeekFeatureEligibilityResult(
        promoted_event_count=len(events),
        distinct_iso_weeks=len(iso_weeks),
        numeric_target_count=numeric_count,
        disease_country_consistent=len(disease_country_pairs) <= 1,
        minimum_weeks_requirement=minimum_weeks,
        feature_generation_allowed=allowed,
        blocker_types=blockers,
        recommended_next_action=(
            "Feature generation gate passed for reviewed promoted events."
            if allowed
            else "Add more reviewed event weeks with explicit numeric targets; do not fabricate missing weeks."
        ),
    )


def _has_numeric_target(event: EpidemicEvent) -> bool:
    return any(
        value is not None
        for value in (
            event.cases,
            event.suspected_cases,
            event.confirmed_cases,
            event.deaths,
            event.hospitalizations,
            event.tests,
            event.positivity_rate,
            event.outbreaks_count,
        )
    )
