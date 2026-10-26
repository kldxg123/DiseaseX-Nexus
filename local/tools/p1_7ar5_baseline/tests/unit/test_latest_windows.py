import datetime as dt
import json

import pytest

from epidemic_core.retrieval.windows import (
    CURRENT_YEAR_TO_LATEST_PERIOD,
    LATEST_COMPLETE_CALENDAR_YEAR,
    LATEST_ROLLING_YEAR,
    WindowResolutionError,
    parse_time_expression,
    resolve_covid_bytes,
    resolve_dengue_bytes,
    resolve_range,
    subtract_calendar_year,
)

NOW = dt.datetime(2026, 7, 12, tzinfo=dt.timezone.utc)


@pytest.mark.parametrize("text", ["最近一年", "最新一年", "过去一年", "过去 12 个月"])
def test_chinese_latest_year_intents(text: str) -> None:
    assert parse_time_expression(text) == LATEST_ROLLING_YEAR


def test_calendar_ranges_and_leap_year() -> None:
    assert subtract_calendar_year(dt.date(2024, 2, 29)) == dt.date(2023, 2, 28)
    assert resolve_range(LATEST_ROLLING_YEAR, dt.date(2026, 6, 21)) == (
        dt.date(2025, 6, 21),
        dt.date(2026, 6, 21),
        "P1Y",
    )
    assert resolve_range(LATEST_COMPLETE_CALENDAR_YEAR, dt.date(2026, 6, 21))[:2] == (
        dt.date(2025, 1, 1),
        dt.date(2025, 12, 31),
    )
    assert resolve_range(CURRENT_YEAR_TO_LATEST_PERIOD, dt.date(2026, 6, 21))[:2] == (
        dt.date(2026, 1, 1),
        dt.date(2026, 6, 21),
    )


def test_covid_new_metric_watermarks_ignore_empty_cumulative_extension() -> None:
    raw = (
        b"Date_reported,Country_code,Country,WHO_region,New_cases,Cumulative_cases,New_deaths,Cumulative_deaths\n"
        b"2026-06-14,FR,France,EURO,2,100,1,10\n"
        b"2026-06-21,FR,France,EURO,0,100,,10\n"
        b"2026-06-28,FR,France,EURO,,100,,10\n"
    )
    result = resolve_covid_bytes(raw, "FR", LATEST_ROLLING_YEAR, NOW)
    assert result.source_declared_max_date == dt.date(2026, 6, 28)
    assert result.source_data_through == dt.date(2026, 6, 21)
    assert result.cases_new_data_through == dt.date(2026, 6, 21)  # explicit zero is reported
    assert result.deaths_new_data_through == dt.date(2026, 6, 14)


def test_dengue_future_all_null_placeholder_does_not_advance_watermark() -> None:
    rows = [
        {
            "COUNTRY": "Brazil",
            "ISO3": "BRA",
            "WHO_REGION": "AMR",
            "YEAR": 2026,
            "DATE_NUM": 23,
            "START_DATE": "2026-06-07",
            "DATE_TYPE": "epiweek",
            "CASES": 0,
            "CONFIRMED_CASES": None,
            "SEVERE_CASES": None,
            "DEATHS": None,
            "SERO_1": None,
            "SERO_2": None,
            "SERO_3": None,
            "SERO_4": None,
            "POPULATION": 1,
        },
        {
            "COUNTRY": "Brazil",
            "ISO3": "BRA",
            "WHO_REGION": "AMR",
            "YEAR": 2026,
            "DATE_NUM": 24,
            "START_DATE": "2026-06-14",
            "DATE_TYPE": "epiweek",
            "CASES": None,
            "CONFIRMED_CASES": None,
            "SEVERE_CASES": None,
            "DEATHS": None,
            "SERO_1": None,
            "SERO_2": None,
            "SERO_3": None,
            "SERO_4": None,
            "POPULATION": 1,
        },
    ]
    raw = json.dumps({"@odata.context": "V_DENGUE_GLOBAL_VALIDATED_PUBLIC", "@odata.count": 2, "value": rows}).encode()
    result = resolve_dengue_bytes(raw, "BRA", LATEST_ROLLING_YEAR, NOW)
    assert result.source_declared_max_date == dt.date(2026, 6, 14)
    assert result.source_data_through == dt.date(2026, 6, 7)
    assert result.cases_new_data_through == dt.date(2026, 6, 7)
    assert result.deaths_freshness_status == "NO_DATA"


def test_no_valid_source_data() -> None:
    raw = b"Date_reported,Country_code,Country,WHO_region,New_cases,Cumulative_cases,New_deaths,Cumulative_deaths\n"
    with pytest.raises(WindowResolutionError, match="NO_VALID_SOURCE_DATA"):
        resolve_covid_bytes(raw, "FR", LATEST_ROLLING_YEAR, NOW)
