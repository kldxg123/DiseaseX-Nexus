from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Any

from cleaner_agent.time_series import (
    add_weeks,
    date_to_iso_week,
    iso_week_start_date,
    make_complete_week_index,
    parse_iso_week,
)


@dataclass
class WeeklyAggregate:
    country: str
    admin1: str | None
    disease: str
    iso_week: str
    week_start_date: date
    cases: int
    suspected_cases: int
    confirmed_cases: int
    deaths: int
    hospitalizations: int
    tests: int
    outbreaks_count: int
    document_count: int
    official_document_count: int
    media_document_count: int
    data_quality_mean: float


def _int_value(value: Any) -> int:
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _float_value(value: Any) -> float:
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _event_cases(event: Any) -> int:
    cases = getattr(event, "cases", None)
    if cases is not None:
        return _int_value(cases)
    return _int_value(getattr(event, "confirmed_cases", None)) + _int_value(
        getattr(event, "suspected_cases", None)
    )


def _event_iso_week(event: Any) -> str | None:
    iso_week = getattr(event, "iso_week", None)
    if iso_week:
        return str(iso_week)
    event_date = getattr(event, "event_date", None)
    if event_date:
        return date_to_iso_week(event_date)
    return None


def _event_quality(event: Any) -> float:
    provenance = getattr(event, "provenance_json", None) or {}
    if isinstance(provenance, dict) and provenance.get("quality_score") is not None:
        return _float_value(provenance.get("quality_score"))
    return _float_value(getattr(event, "confidence_score", None))


def _source_category(event: Any) -> str | None:
    provenance = getattr(event, "provenance_json", None) or {}
    if not isinstance(provenance, dict):
        return None
    return provenance.get("source_type") or provenance.get("source_category")


def _empty_aggregate(
    *,
    country: str,
    admin1: str | None,
    disease: str,
    iso_week: str,
) -> WeeklyAggregate:
    return WeeklyAggregate(
        country=country,
        admin1=admin1,
        disease=disease,
        iso_week=iso_week,
        week_start_date=iso_week_start_date(iso_week),
        cases=0,
        suspected_cases=0,
        confirmed_cases=0,
        deaths=0,
        hospitalizations=0,
        tests=0,
        outbreaks_count=0,
        document_count=0,
        official_document_count=0,
        media_document_count=0,
        data_quality_mean=0.0,
    )


def aggregate_events_weekly(
    events: list,
    *,
    fill_missing_weeks: bool = True,
) -> list[WeeklyAggregate]:
    buckets: dict[tuple[str, str | None, str, str], dict[str, Any]] = {}
    for event in events:
        country = getattr(event, "country", None)
        disease = getattr(event, "disease", None)
        iso_week = _event_iso_week(event)
        if not country or not disease or not iso_week:
            continue
        parse_iso_week(iso_week)
        admin1 = getattr(event, "admin1", None)
        key = (str(country), admin1, str(disease), iso_week)
        bucket = buckets.setdefault(
            key,
            {
                "cases": 0,
                "suspected_cases": 0,
                "confirmed_cases": 0,
                "deaths": 0,
                "hospitalizations": 0,
                "tests": 0,
                "outbreaks_count": 0,
                "document_count": 0,
                "official_document_count": 0,
                "media_document_count": 0,
                "quality_values": [],
            },
        )
        bucket["cases"] += _event_cases(event)
        bucket["suspected_cases"] += _int_value(getattr(event, "suspected_cases", None))
        bucket["confirmed_cases"] += _int_value(getattr(event, "confirmed_cases", None))
        bucket["deaths"] += _int_value(getattr(event, "deaths", None))
        bucket["hospitalizations"] += _int_value(getattr(event, "hospitalizations", None))
        bucket["tests"] += _int_value(getattr(event, "tests", None))
        bucket["outbreaks_count"] += _int_value(getattr(event, "outbreaks_count", None))
        bucket["document_count"] += 1
        category = _source_category(event)
        if category in {"official_page", "official_dataset"}:
            bucket["official_document_count"] += 1
        elif category in {"news_candidate", "news_index", "media"}:
            bucket["media_document_count"] += 1
        bucket["quality_values"].append(_event_quality(event))

    aggregates_by_key: dict[tuple[str, str | None, str, str], WeeklyAggregate] = {}
    for key, bucket in buckets.items():
        country, admin1, disease, iso_week = key
        quality_values = bucket["quality_values"]
        aggregate = WeeklyAggregate(
            country=country,
            admin1=admin1,
            disease=disease,
            iso_week=iso_week,
            week_start_date=iso_week_start_date(iso_week),
            cases=bucket["cases"],
            suspected_cases=bucket["suspected_cases"],
            confirmed_cases=bucket["confirmed_cases"],
            deaths=bucket["deaths"],
            hospitalizations=bucket["hospitalizations"],
            tests=bucket["tests"],
            outbreaks_count=bucket["outbreaks_count"],
            document_count=bucket["document_count"],
            official_document_count=bucket["official_document_count"],
            media_document_count=bucket["media_document_count"],
            data_quality_mean=sum(quality_values) / len(quality_values) if quality_values else 0.0,
        )
        aggregates_by_key[key] = aggregate

    if fill_missing_weeks:
        group_weeks: dict[tuple[str, str | None, str], list[str]] = defaultdict(list)
        for country, admin1, disease, iso_week in aggregates_by_key:
            group_weeks[(country, admin1, disease)].append(iso_week)
        for group_key, weeks in group_weeks.items():
            ordered = sorted(weeks, key=iso_week_start_date)
            for iso_week in make_complete_week_index(ordered[0], ordered[-1]):
                full_key = (*group_key, iso_week)
                if full_key not in aggregates_by_key:
                    country, admin1, disease = group_key
                    aggregates_by_key[full_key] = _empty_aggregate(
                        country=country,
                        admin1=admin1,
                        disease=disease,
                        iso_week=iso_week,
                    )

    return sorted(
        aggregates_by_key.values(),
        key=lambda item: (item.country, item.admin1 or "", item.disease, item.week_start_date),
    )


def _group_aggregates(
    aggregates: list[WeeklyAggregate],
) -> dict[tuple[str, str | None, str], list[WeeklyAggregate]]:
    groups: dict[tuple[str, str | None, str], list[WeeklyAggregate]] = defaultdict(list)
    for aggregate in aggregates:
        groups[(aggregate.country, aggregate.admin1, aggregate.disease)].append(aggregate)
    for key in groups:
        groups[key].sort(key=lambda item: item.week_start_date)
    return groups


def _sum(values: list[int]) -> int:
    return int(sum(values))


def _mean(values: list[int]) -> float:
    return float(sum(values) / len(values)) if values else 0.0


def _feature_json(series: list[WeeklyAggregate], index: int) -> dict:
    current = series[index]

    def lag(offset: int, attr: str) -> int:
        previous_index = index - offset
        if previous_index < 0:
            return 0
        return _int_value(getattr(series[previous_index], attr))

    rolling_4 = series[max(0, index - 3) : index + 1]
    rolling_8 = series[max(0, index - 7) : index + 1]
    _, week_number = parse_iso_week(current.iso_week)
    angle = 2 * math.pi * (week_number / 53.0)

    return {
        "current_week_cases": current.cases,
        "current_week_outbreaks": current.outbreaks_count,
        "current_week_deaths": current.deaths,
        "current_week_hospitalizations": current.hospitalizations,
        "current_week_tests": current.tests,
        "lag_1w_cases": lag(1, "cases"),
        "lag_2w_cases": lag(2, "cases"),
        "lag_4w_cases": lag(4, "cases"),
        "lag_1w_outbreaks": lag(1, "outbreaks_count"),
        "lag_2w_outbreaks": lag(2, "outbreaks_count"),
        "lag_4w_outbreaks": lag(4, "outbreaks_count"),
        "rolling_4w_mean_cases": _mean([item.cases for item in rolling_4]),
        "rolling_8w_mean_cases": _mean([item.cases for item in rolling_8]),
        "rolling_4w_sum_outbreaks": _sum([item.outbreaks_count for item in rolling_4]),
        "rolling_8w_sum_outbreaks": _sum([item.outbreaks_count for item in rolling_8]),
        "media_document_count": current.media_document_count,
        "official_document_count": current.official_document_count,
        "data_quality_mean": float(current.data_quality_mean),
        "season_week_sin": float(math.sin(angle)),
        "season_week_cos": float(math.cos(angle)),
        "holiday_flag": False,
    }


def build_features_from_weekly_aggregates(
    aggregates: list[WeeklyAggregate],
    *,
    horizons_days: tuple[int, ...] = (7, 28),
    outbreak_threshold: int = 3,
    split_type: str = "train",
) -> list[dict]:
    rows: list[dict] = []
    for (_country, _admin1, _disease), series in _group_aggregates(aggregates).items():
        for index, aggregate in enumerate(series):
            for horizon_days in horizons_days:
                if horizon_days <= 0 or horizon_days % 7 != 0:
                    raise ValueError("horizons_days values must be positive multiples of 7.")
                horizon_weeks = horizon_days // 7
                future_window = series[index + 1 : index + 1 + horizon_weeks]
                if len(future_window) < horizon_weeks:
                    continue
                target_cases = sum(item.cases for item in future_window)
                target_outbreaks = sum(item.outbreaks_count for item in future_window)
                rows.append(
                    {
                        "country": aggregate.country,
                        "admin1": aggregate.admin1,
                        "disease": aggregate.disease,
                        "target_date": add_weeks(aggregate.week_start_date, horizon_weeks),
                        "iso_week": aggregate.iso_week,
                        "horizon_days": int(horizon_days),
                        "feature_json": _feature_json(series, index),
                        "target_value": float(target_cases),
                        "target_outbreak_label": target_outbreaks >= outbreak_threshold,
                        "split_type": split_type,
                    }
                )
    return rows
