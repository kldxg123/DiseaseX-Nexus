from __future__ import annotations

from datetime import date, datetime

import pytest

from cleaner_agent.time_series import (
    add_weeks,
    date_to_iso_week,
    iso_week_start_date,
    make_complete_week_index,
    parse_iso_week,
)


def test_iso_week_parse_start_date_and_add_weeks() -> None:
    assert parse_iso_week("2025-W05") == (2025, 5)
    assert iso_week_start_date("2025-W05") == date(2025, 1, 27)
    assert date_to_iso_week(datetime(2025, 1, 31, 12, 0, 0)) == "2025-W05"
    assert add_weeks(date(2025, 1, 27), 2) == date(2025, 2, 10)


def test_make_complete_week_index_fills_missing_weeks() -> None:
    assert make_complete_week_index("2025-W01", "2025-W04") == [
        "2025-W01",
        "2025-W02",
        "2025-W03",
        "2025-W04",
    ]


def test_invalid_iso_week_raises_value_error() -> None:
    with pytest.raises(ValueError):
        parse_iso_week("2025-05")
    with pytest.raises(ValueError):
        make_complete_week_index("2025-W04", "2025-W01")
