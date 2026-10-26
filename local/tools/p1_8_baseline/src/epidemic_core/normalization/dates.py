from __future__ import annotations

import datetime as dt
import re

from epidemic_core.errors import EpidemicCoreError

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_iso_date(value: str) -> dt.date:
    if not _ISO_DATE.fullmatch(value):
        raise EpidemicCoreError("INVALID_DATE")
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise EpidemicCoreError("INVALID_DATE") from exc


def parse_date_range(start: str, end: str) -> tuple[dt.date, dt.date]:
    a, b = parse_iso_date(start), parse_iso_date(end)
    if a > b:
        raise EpidemicCoreError("INVALID_DATE_RANGE")
    return a, b
