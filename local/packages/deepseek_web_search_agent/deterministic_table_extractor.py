from __future__ import annotations

import re
from datetime import date
from typing import Any

from deepseek_web_search_agent.contextual_evidence_builder import build_contextual_evidence_pack
from deepseek_web_search_agent.table_response_parser import parse_int_or_none


def deterministic_rows_from_sources(sources, request) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for source in sources:
        for preview in list(getattr(source, "table_preview_rows", []) or [])[:5]:
            if not isinstance(preview, dict):
                continue
            row = _row_from_preview(preview, source, request, index=len(rows) + 1)
            if row is not None:
                rows.append(row)
        for signal in list(getattr(source, "paragraph_signal_preview", []) or [])[:10]:
            if not isinstance(signal, dict):
                continue
            row = _row_from_paragraph(signal, source, request, index=len(rows) + 1)
            if row is not None:
                rows.append(row)
    if rows:
        return rows
    seen = {_row_key(row) for row in rows}
    contextual = build_contextual_evidence_pack(sources, date_start=request.date_start, date_end=request.date_end)
    for candidate in contextual.linked_candidates:
        row = _row_from_context_candidate(candidate, request, index=len(rows) + 1)
        if row is None:
            continue
        key = _row_key(row)
        if key in seen:
            continue
        seen.add(key)
        rows.append(row)
    return rows


def _row_from_preview(preview: dict[str, Any], source, request, *, index: int) -> dict[str, Any] | None:
    date_value = None
    region = None
    numeric_values: dict[str, int | None] = {
        "confirmed_cases": None,
        "confirmed_deaths": None,
        "suspected_cases": None,
        "contacts_under_monitoring": None,
    }
    for raw_key, raw_value in preview.items():
        key = str(raw_key or "").lower()
        value = str(raw_value or "").strip()
        if not value:
            continue
        if date_value is None and any(term in key for term in ("date", "week", "mmwr", "report")):
            date_value = normalize_evidence_date(value, request.date_start)
        elif region is None and any(term in key for term in ("state", "region", "jurisdiction", "location", "country")):
            region = value[:120]
        if any(term in key for term in ("death", "fatal")):
            numeric_values["confirmed_deaths"] = parse_int_or_none(value)
        elif any(term in key for term in ("suspect", "probable")):
            numeric_values["suspected_cases"] = parse_int_or_none(value)
        elif "contact" in key:
            numeric_values["contacts_under_monitoring"] = parse_int_or_none(value)
        elif any(term in key for term in ("case", "positive", "count", "total", "confirmed")):
            numeric_values["confirmed_cases"] = parse_int_or_none(value)
    return _build_row(date_value, region, numeric_values, source, request, index=index, mode="table_preview")


def _row_from_paragraph(signal: dict[str, Any], source, request, *, index: int) -> dict[str, Any] | None:
    text = str(signal.get("text") or "")[:260]
    if not text:
        return None
    date_value = normalize_evidence_date(text, request.date_start)
    numeric_values = {
        "confirmed_cases": _metric_number(text, r"(?:confirmed\s+)?cases?"),
        "confirmed_deaths": _metric_number(text, r"deaths?|fatalit(?:y|ies)"),
        "suspected_cases": _metric_number(text, r"suspected\s+cases?|probable\s+cases?"),
        "contacts_under_monitoring": _metric_number(text, r"contacts?(?:\s+under\s+monitoring)?|monitoring"),
    }
    if numeric_values["confirmed_cases"] is None:
        numeric_values["confirmed_cases"] = _metric_number(text, r"confirmed")
    region = _region_hint(text, request.country_or_region)
    return _build_row(date_value, region, numeric_values, source, request, index=index, mode="paragraph_signal_preview")


def _build_row(
    date_value: str | None,
    region: str | None,
    numeric_values: dict[str, int | None],
    source,
    request,
    *,
    index: int,
    mode: str,
) -> dict[str, Any] | None:
    if not date_value or not any(value is not None for value in numeric_values.values()):
        return None
    return {
        "row_id": f"ROW{index}",
        "date": date_value,
        "country": request.country_or_region,
        "region": region or request.country_or_region,
        "source_id": source.source_id,
        "evidence_summary": f"Deterministic {mode} row from {source.source_id}; human review required.",
        "confidence": 0.68 if mode == "table_preview" else 0.62,
        "uncertainty_flags": [mode, "human_review_required"],
        "date_source": "explicit_text" if mode == "paragraph_signal_preview" else "table_caption_date",
        "date_anchor_id": f"{source.source_id}:date:{index}",
        "metric_anchor_id": f"{source.source_id}:metric:{index}",
        "date_confidence": "high",
        "metric_source": mode,
        "requires_human_review": True,
        **numeric_values,
    }


def _row_from_context_candidate(candidate, request, *, index: int) -> dict[str, Any] | None:
    date_value = normalize_evidence_date(str(candidate.date_value or ""), request.date_start)
    if date_value is None:
        return None
    numeric_values: dict[str, int | None] = {
        "confirmed_cases": None,
        "confirmed_deaths": None,
        "suspected_cases": None,
        "contacts_under_monitoring": None,
    }
    field = candidate.metric_name if candidate.metric_name in numeric_values else "confirmed_cases"
    numeric_values[field] = int(candidate.metric_value)
    flags = list(candidate.uncertainty_flags)
    if candidate.date_source == "report_date_fallback":
        flags.append("low_confidence_date_anchor")
    return {
        "row_id": f"ROW{index}",
        "date": date_value,
        "country": request.country_or_region,
        "region": request.country_or_region,
        "source_id": candidate.source_id,
        "evidence_summary": candidate.evidence_summary[:500],
        "confidence": min(0.68, max(0.4, float(candidate.relation_confidence))),
        "uncertainty_flags": sorted(set(flags)),
        "date_source": candidate.date_source,
        "date_anchor_id": candidate.date_anchor_id,
        "metric_anchor_id": candidate.metric_anchor_id,
        "date_confidence": "low" if candidate.date_source == "report_date_fallback" else "medium",
        "metric_source": "context_metric_anchor",
        "requires_human_review": True,
        **numeric_values,
    }


def _row_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        row.get("date"),
        row.get("source_id"),
        row.get("confirmed_cases"),
        row.get("confirmed_deaths"),
        row.get("suspected_cases"),
        row.get("contacts_under_monitoring"),
    )


def normalize_evidence_date(value: str, fallback_start: str) -> str | None:
    text = str(value or "").strip()
    match = re.search(r"\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b", text)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3))).isoformat()
        except ValueError:
            return None
    month_match = re.search(
        r"\b(\d{1,2})\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+(20\d{2})\b",
        text,
        flags=re.I,
    )
    if not month_match:
        month_match = re.search(
            r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+(\d{1,2}),?\s+(20\d{2})\b",
            text,
            flags=re.I,
        )
        if month_match:
            month = _month_number(month_match.group(1))
            day = int(month_match.group(2))
            year = int(month_match.group(3))
        else:
            month = day = year = None
    else:
        day = int(month_match.group(1))
        month = _month_number(month_match.group(2))
        year = int(month_match.group(3))
    if month and day and year:
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            return None
    week_match = re.search(r"\b(?:week|mmwr)\s*(\d{1,2})\b", text, flags=re.I)
    if week_match:
        try:
            year = int(str(fallback_start)[:4])
            return date.fromisocalendar(year, int(week_match.group(1)), 1).isoformat()
        except ValueError:
            return None
    return None


def _metric_number(text: str, keyword_pattern: str) -> int | None:
    candidates: list[tuple[int, int]] = []
    for keyword in re.finditer(rf"\b{keyword_pattern}\b", text, flags=re.I):
        before_window = text[max(0, keyword.start() - 55) : keyword.start()]
        after_window = text[keyword.end() : keyword.end() + 55]
        before_numbers = list(re.finditer(r"\b\d{1,3}(?:,\d{3})*\b", before_window))
        if before_numbers:
            match = before_numbers[-1]
            candidates.append((keyword.start() - match.end(), parse_int_or_none(match.group(0)) or 0))
        after_number = re.search(r"\b\d{1,3}(?:,\d{3})*\b", after_window)
        if after_number:
            candidates.append((after_number.start(), parse_int_or_none(after_number.group(0)) or 0))
    if candidates:
        return sorted(candidates, key=lambda item: item[0])[0][1]
    return None


def _region_hint(text: str, fallback: str) -> str:
    match = re.search(r"\bin\s+([A-Z][A-Za-z .'-]{2,60})\b", text)
    if match:
        return match.group(1).strip()[:120]
    return fallback


def _month_number(value: str) -> int | None:
    months = {
        "jan": 1,
        "feb": 2,
        "mar": 3,
        "apr": 4,
        "may": 5,
        "jun": 6,
        "jul": 7,
        "aug": 8,
        "sep": 9,
        "oct": 10,
        "nov": 11,
        "dec": 12,
    }
    return months.get(str(value or "")[:3].lower())


__all__ = ["deterministic_rows_from_sources", "normalize_evidence_date"]
