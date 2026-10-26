from __future__ import annotations

from datetime import date, datetime

from cleaner_agent.date_normalization import iso_week_from_date, parse_event_date


def test_parse_event_date_iso_english_chinese_and_fallback() -> None:
    assert parse_event_date("Reported on 2025-01-31") == date(2025, 1, 31)
    assert parse_event_date("Reported on 2025/01/31") == date(2025, 1, 31)
    assert parse_event_date("January 31, 2025 bulletin") == date(2025, 1, 31)
    assert parse_event_date("Jan 31 2025 bulletin") == date(2025, 1, 31)
    assert parse_event_date("2025年1月31日报告") == date(2025, 1, 31)
    assert parse_event_date("no date", fallback=datetime(2025, 2, 1, 1, 2, 3)) == date(
        2025,
        2,
        1,
    )


def test_iso_week_from_date_format() -> None:
    assert iso_week_from_date(date(2025, 1, 31)) == "2025-W05"
    assert iso_week_from_date(None) is None
