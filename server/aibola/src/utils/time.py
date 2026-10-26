from __future__ import annotations

from datetime import date, datetime, timezone


def parse_date(value: str | date | datetime) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if len(text) == 4 and text.isdigit():
        return date(int(text), 1, 1)
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%d/%m/%Y", "%Y-%m", "%Y/%m"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(text[:10]).date()
    except ValueError:
        try:
            from dateutil.parser import parse  # type: ignore

            return parse(text).date()
        except Exception as exc:
            raise ValueError(f"Could not parse date: {text}") from exc


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
