from __future__ import annotations

import re
from datetime import date, datetime


MONTHS = {
    "january": 1,
    "jan": 1,
    "february": 2,
    "feb": 2,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "june": 6,
    "jun": 6,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sep": 9,
    "sept": 9,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
}


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _fallback_date(fallback: datetime | date | None) -> date | None:
    if isinstance(fallback, datetime):
        return fallback.date()
    if isinstance(fallback, date):
        return fallback
    return None


def parse_event_date(text: str, fallback: datetime | date | None = None) -> date | None:
    text = text or ""
    iso_match = re.search(r"\b(\d{4})[-/](\d{1,2})[-/](\d{1,2})\b", text)
    if iso_match:
        parsed = _safe_date(*(int(part) for part in iso_match.groups()))
        if parsed:
            return parsed

    english_match = re.search(
        r"\b([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})\b",
        text,
        flags=re.IGNORECASE,
    )
    if english_match:
        month_name, day, year = english_match.groups()
        month = MONTHS.get(month_name.lower())
        if month:
            parsed = _safe_date(int(year), month, int(day))
            if parsed:
                return parsed

    chinese_match = re.search(r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日", text)
    if chinese_match:
        parsed = _safe_date(*(int(part) for part in chinese_match.groups()))
        if parsed:
            return parsed

    return _fallback_date(fallback)


def iso_week_from_date(value: date | datetime | None) -> str | None:
    parsed = _fallback_date(value)
    if parsed is None:
        return None
    iso_year, iso_week, _ = parsed.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"
