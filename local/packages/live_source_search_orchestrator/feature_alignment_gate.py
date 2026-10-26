from __future__ import annotations

from collections import Counter
from datetime import date, datetime
from typing import Any

from cleaner_agent.time_series import parse_iso_week

NUMERIC_FIELDS = (
    "cases",
    "confirmed_cases",
    "suspected_cases",
    "outbreaks_count",
    "tests",
    "positivity_rate",
    "hospitalizations",
)


def check_weekly_alignment_ready(events: list[Any]) -> dict[str, Any]:
    total = len(events)
    missing_iso_week = 0
    invalid_iso_week = 0
    weeks: set[str] = set()
    for event in events:
        iso_week = _value(event, "iso_week")
        if not iso_week:
            missing_iso_week += 1
            continue
        try:
            parse_iso_week(str(iso_week))
            weeks.add(str(iso_week))
        except ValueError:
            invalid_iso_week += 1
    ready = total > 0 and missing_iso_week == 0 and invalid_iso_week == 0
    return {
        "ready": ready,
        "event_count": total,
        "distinct_week_count": len(weeks),
        "missing_iso_week_count": missing_iso_week,
        "invalid_iso_week_count": invalid_iso_week,
        "weeks": sorted(weeks),
        "blocker_type": None if ready else "insufficient_temporal_signal",
    }


def check_minimum_time_series_length(events: list[Any], min_weeks: int = 4) -> dict[str, Any]:
    alignment = check_weekly_alignment_ready(events)
    distinct_weeks = int(alignment["distinct_week_count"])
    ready = bool(alignment["ready"]) and distinct_weeks >= int(min_weeks)
    return {
        "ready": ready,
        "distinct_week_count": distinct_weeks,
        "minimum_required_weeks": int(min_weeks),
        "blocker_type": None if ready else "insufficient_temporal_signal",
    }


def check_numeric_target_ready(events: list[Any]) -> dict[str, Any]:
    numeric_events = 0
    field_counts: Counter[str] = Counter()
    for event in events:
        fields = [field for field in NUMERIC_FIELDS if _has_numeric(event, field)]
        if fields:
            numeric_events += 1
            field_counts.update(fields)
    ready = len(events) > 0 and numeric_events > 0
    return {
        "ready": ready,
        "event_count": len(events),
        "numeric_event_count": numeric_events,
        "numeric_field_counts": dict(sorted(field_counts.items())),
        "blocker_type": None if ready else "insufficient_numeric_signal",
    }


def check_horizon_label_ready(events: list[Any], horizons: tuple[int, ...] = (7, 14, 28)) -> dict[str, Any]:
    alignment = check_weekly_alignment_ready(events)
    distinct_weeks = int(alignment["distinct_week_count"])
    horizon_results: dict[str, dict[str, Any]] = {}
    for horizon in horizons:
        horizon_weeks = max(1, int(horizon) // 7)
        required_weeks = horizon_weeks + 1
        horizon_results[str(int(horizon))] = {
            "ready": bool(alignment["ready"]) and distinct_weeks >= required_weeks,
            "required_weeks": required_weeks,
            "observed_weeks": distinct_weeks,
        }
    ready = any(item["ready"] for item in horizon_results.values())
    return {
        "ready": ready,
        "distinct_week_count": distinct_weeks,
        "horizons": horizon_results,
        "blocker_type": None if ready else "insufficient_target_signal",
    }


def build_feature_generation_blocker_summary(events: list[Any]) -> dict[str, Any]:
    weekly = check_weekly_alignment_ready(events)
    minimum = check_minimum_time_series_length(events)
    numeric = check_numeric_target_ready(events)
    horizon = check_horizon_label_ready(events)
    blockers: list[str] = []
    if not weekly["ready"] or not minimum["ready"]:
        blockers.append("insufficient_temporal_signal")
    if not numeric["ready"]:
        blockers.append("insufficient_numeric_signal")
    if not horizon["ready"]:
        blockers.append("insufficient_target_signal")
    if _single_page_or_summary_like(events):
        blockers.append("single_page_summary_signal")
    return {
        "eligible": not blockers,
        "blocker_types": sorted(set(blockers)),
        "weekly_alignment": weekly,
        "minimum_time_series": minimum,
        "numeric_target": numeric,
        "horizon_label": horizon,
        "single_page_summary_risk": _single_page_or_summary_like(events),
    }


def _single_page_or_summary_like(events: list[Any]) -> bool:
    if not events:
        return False
    sources = {
        _value(getattr(event, "raw_document", None), "normalized_url")
        or _value(getattr(event, "raw_document", None), "original_url")
        or _value(event, "raw_document_id")
        for event in events
    }
    weeks = {_value(event, "iso_week") for event in events if _value(event, "iso_week")}
    return len(events) >= 2 and len(weeks) <= 1 and len(sources) <= max(1, len(events))


def _has_numeric(event: Any, field: str) -> bool:
    value = _value(event, field)
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _value(event: Any, field: str) -> Any:
    if event is None:
        return None
    if isinstance(event, dict):
        return event.get(field)
    value = getattr(event, field, None)
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return value


__all__ = [
    "NUMERIC_FIELDS",
    "build_feature_generation_blocker_summary",
    "check_horizon_label_ready",
    "check_minimum_time_series_length",
    "check_numeric_target_ready",
    "check_weekly_alignment_ready",
]
