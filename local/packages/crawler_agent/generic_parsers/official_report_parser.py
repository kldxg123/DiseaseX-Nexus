from __future__ import annotations

import re
from datetime import date

from crawler_agent.content_extraction import extract_content
from crawler_agent.generic_parsers.types import (
    ParsedRecord,
    ParserResult,
    has_numeric_signal,
    parse_float,
    parse_int,
    squash,
)
from crawler_agent.url_utils import normalize_url

MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


def parse_official_report(
    content: str,
    *,
    source_name: str,
    url: str,
    country: str | None = None,
    disease: str | None = None,
) -> ParserResult:
    extracted = extract_content(content or "")
    text = squash(extracted.body_text)
    title = extracted.title or source_name
    if not text:
        return ParserResult("official_report_page", [], ["Official report page has no extracted text."], skipped_rows=0)
    structured = _extract_structured_fields(text, country=country, disease=disease)
    warnings: list[str] = []
    if not structured.get("event_date") and not structured.get("iso_week"):
        warnings.append("Official report page has no recognized date or week.")
    if not has_numeric_signal(structured):
        warnings.append("Official report page has no recognized numeric surveillance field.")
    record = ParsedRecord(
        source_name=source_name,
        source_type="official_report_page",
        parser_strategy="official_report_page",
        url=normalize_url(url),
        title=title,
        text=text,
        country_detected=country,
        disease_detected=disease,
        language="en",
        structured_fields=structured,
        metadata={
            "row_number": 1,
            "paragraph_count": max(1, text.count(". ")),
            "content_kind": "official_report_page",
            "field_names": sorted(structured),
            "field_count": len(structured),
        },
    )
    return ParserResult("official_report_page", [record], warnings, skipped_rows=0)


def _extract_structured_fields(text: str, *, country: str | None, disease: str | None) -> dict:
    structured: dict = {
        "country": country,
        "disease": disease.lower() if disease else None,
        "source_type": "official_report_page",
    }
    event_date = _extract_event_date(text)
    if event_date:
        structured["event_date"] = event_date.isoformat()
    for key, patterns in {
        "confirmed_cases": [r"\b([\d,]+)\s+confirmed\s+cases?\b"],
        "suspected_cases": [r"\b([\d,]+)\s+suspected\s+cases?\b"],
        "cases": [r"\b([\d,]+)\s+(?:influenza\s+)?cases?\b"],
        "tests": [r"\b([\d,]+)\s+tests?\b"],
        "hospitalizations": [r"\b([\d,]+)\s+hospitali[sz]ations?\b"],
        "deaths": [r"\b([\d,]+)\s+deaths?\b"],
        "outbreaks_count": [r"\b([\d,]+)\s+outbreaks?\b"],
    }.items():
        parsed = _first_int(text, patterns)
        if parsed is not None:
            structured[key] = parsed
    positivity = _first_float(
        text,
        [
            r"\b(\d+(?:\.\d+)?)%\s+positivity\b",
            r"\bpositivity\s+rate\s+of\s+(\d+(?:\.\d+)?)%",
            r"\bpercent\s+positive\s+(\d+(?:\.\d+)?)%",
        ],
    )
    if positivity is not None:
        structured["positivity_rate"] = positivity
    return {key: value for key, value in structured.items() if value is not None}


def _extract_event_date(text: str) -> date | None:
    match = re.search(
        r"\b("
        + "|".join(MONTHS)
        + r")\s+(\d{1,2}),\s+(\d{4})\b",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    month = MONTHS.get(match.group(1).lower())
    if not month:
        return None
    try:
        return date(int(match.group(3)), month, int(match.group(2)))
    except ValueError:
        return None


def _first_int(text: str, patterns: list[str]) -> int | None:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            parsed = parse_int(match.group(1))
            if parsed is not None:
                return parsed
    return None


def _first_float(text: str, patterns: list[str]) -> float | None:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            parsed = parse_float(match.group(1))
            if parsed is not None:
                return parsed
    return None
