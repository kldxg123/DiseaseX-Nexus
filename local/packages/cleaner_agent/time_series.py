from __future__ import annotations

import re
from datetime import date, datetime, timedelta


ISO_WEEK_PATTERN = re.compile(r"^(\d{4})-W(\d{2})$")


def parse_iso_week(iso_week: str) -> tuple[int, int]:
    match = ISO_WEEK_PATTERN.match((iso_week or "").strip())
    if not match:
        raise ValueError("iso_week must use YYYY-Www format.")
    year, week = (int(part) for part in match.groups())
    if week < 1 or week > 53:
        raise ValueError("iso_week week number must be between 1 and 53.")
    try:
        date.fromisocalendar(year, week, 1)
    except ValueError as exc:
        raise ValueError("iso_week is not a valid ISO week.") from exc
    return year, week


def iso_week_start_date(iso_week: str) -> date:
    year, week = parse_iso_week(iso_week)
    return date.fromisocalendar(year, week, 1)


def date_to_iso_week(value: date | datetime) -> str:
    parsed = value.date() if isinstance(value, datetime) else value
    iso_year, iso_week, _ = parsed.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def add_weeks(value: date, weeks: int) -> date:
    return value + timedelta(days=7 * int(weeks))


def make_complete_week_index(start_week: str, end_week: str) -> list[str]:
    start_date = iso_week_start_date(start_week)
    end_date = iso_week_start_date(end_week)
    if start_date > end_date:
        raise ValueError("start_week must be before or equal to end_week.")
    weeks: list[str] = []
    current = start_date
    while current <= end_date:
        weeks.append(date_to_iso_week(current))
        current = add_weeks(current, 1)
    return weeks
