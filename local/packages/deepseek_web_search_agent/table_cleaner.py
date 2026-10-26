from __future__ import annotations

from datetime import date
from typing import Any

from deepseek_web_search_agent.structured_table_types import DiseaseTimeQueryRequest, ExtractedEventTableRow
from deepseek_web_search_agent.table_response_parser import NUMERIC_FIELDS, parse_int_or_none


def clean_extracted_table_rows(
    rows: list[dict[str, Any]],
    query_request: DiseaseTimeQueryRequest,
) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        flags = list(row.get("uncertainty_flags") or [])
        normalized_date = normalize_date(row.get("date"))
        if normalized_date is None:
            flags.append("date_uncertain")
        values = {field: parse_int_or_none(row.get(field)) for field in NUMERIC_FIELDS}
        event = ExtractedEventTableRow(
            row_id=str(row.get("row_id") or f"ROW{index}"),
            date=normalized_date,
            country=normalize_country(row.get("country") or query_request.country_or_region),
            region=normalize_region(row.get("region")),
            confirmed_cases=values["confirmed_cases"],
            confirmed_deaths=values["confirmed_deaths"],
            suspected_cases=values["suspected_cases"],
            contacts_under_monitoring=values["contacts_under_monitoring"],
            source_id=_str_or_none(row.get("source_id")),
            disease=query_request.disease,
            extraction_confidence=_confidence(row.get("confidence")),
            evidence_summary=str(row.get("evidence_summary") or "")[:500],
            uncertainty_flags=sorted(set(flags)),
            date_source=_str_or_none(row.get("date_source")),
            date_anchor_id=_str_or_none(row.get("date_anchor_id")),
            metric_anchor_id=_str_or_none(row.get("metric_anchor_id")),
            date_confidence=_str_or_none(row.get("date_confidence")),
            metric_source=_str_or_none(row.get("metric_source")),
            requires_human_review=True,
            approval_status=str(row.get("approval_status") or "pending_review"),
            reviewer_note=_str_or_none(row.get("reviewer_note")),
            validation_status="needs_validation",
        )
        cleaned.append(event.to_dict())
    return cleaned


def normalize_date(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        return None


def normalize_country(value: Any) -> str | None:
    text = _str_or_none(value)
    if text is None:
        return None
    aliases = {
        "usa": "United States",
        "u.s.": "United States",
        "us": "United States",
        "uk": "United Kingdom",
    }
    return aliases.get(text.lower(), " ".join(text.split()))


def normalize_region(value: Any) -> str | None:
    text = _str_or_none(value)
    if text is None:
        return None
    return " ".join(text.split())


def _confidence(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
