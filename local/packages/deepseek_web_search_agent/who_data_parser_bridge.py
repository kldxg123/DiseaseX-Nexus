from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass, field
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from deepseek_web_search_agent.deterministic_table_extractor import normalize_evidence_date
from deepseek_web_search_agent.source_pack_builder import source_pack_readiness
from deepseek_web_search_agent.staging_review import write_structured_table_review
from deepseek_web_search_agent.structured_table_types import DiseaseTimeQueryRequest, SourceEvidenceItem
from deepseek_web_search_agent.table_cleaner import clean_extracted_table_rows
from deepseek_web_search_agent.table_deduper import find_duplicate_event_rows
from deepseek_web_search_agent.table_validator import validate_extracted_table_rows


@dataclass(frozen=True)
class WHOFieldMappingReport:
    source_id: str
    mapped_fields: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "mapped_fields": dict(self.mapped_fields),
            "warnings": list(self.warnings),
        }


CANONICAL_FIELDS = {
    "date",
    "country",
    "region",
    "confirmed_cases",
    "confirmed_deaths",
    "suspected_cases",
    "contacts_under_monitoring",
}


def normalize_who_field_name(name: str) -> str | None:
    key = re.sub(r"[^a-z0-9]+", "_", str(name or "").strip().lower()).strip("_")
    if not key:
        return None
    if any(term in key for term in ("date", "week", "epi_week", "reporting_period")):
        return "date"
    if key in {"country", "country_area", "member_state"}:
        return "country"
    if any(term in key for term in ("province", "region", "health_zone", "zone", "location", "district", "admin")):
        return "region"
    if "death" in key or "fatal" in key:
        return "confirmed_deaths"
    if "contact" in key:
        return "contacts_under_monitoring"
    if "suspect" in key or "probable" in key:
        return "suspected_cases"
    if any(term in key for term in ("confirmed", "case", "total_cases", "cumulative_cases")):
        return "confirmed_cases"
    return None


def rows_from_csv_preview(
    csv_text: str,
    *,
    query_request: DiseaseTimeQueryRequest,
    source_id: str,
    max_rows: int = 50,
) -> tuple[list[dict[str, Any]], WHOFieldMappingReport]:
    reader = csv.DictReader(io.StringIO(str(csv_text or "")))
    return rows_from_table_records(
        list(reader)[:max_rows],
        query_request=query_request,
        source_id=source_id,
        material_type="csv_preview",
    )


def rows_from_html_table(
    html_text: str,
    *,
    query_request: DiseaseTimeQueryRequest,
    source_id: str,
    max_rows: int = 50,
) -> tuple[list[dict[str, Any]], WHOFieldMappingReport]:
    parser = _TableParser()
    parser.feed(str(html_text or ""))
    records = parser.records()[:max_rows]
    return rows_from_table_records(
        records,
        query_request=query_request,
        source_id=source_id,
        material_type="html_table_preview",
    )


def rows_from_json_payload(
    payload: str | dict[str, Any] | list[Any],
    *,
    query_request: DiseaseTimeQueryRequest,
    source_id: str,
    records_path: str | None = None,
    max_rows: int = 50,
) -> tuple[list[dict[str, Any]], WHOFieldMappingReport]:
    value: Any = payload
    if isinstance(payload, str):
        try:
            value = json.loads(payload)
        except json.JSONDecodeError:
            value = []
    records = _json_records(value, records_path=records_path)[:max_rows]
    return rows_from_table_records(
        records,
        query_request=query_request,
        source_id=source_id,
        material_type="json_preview",
    )


def rows_from_table_records(
    records: list[dict[str, Any]],
    *,
    query_request: DiseaseTimeQueryRequest,
    source_id: str,
    material_type: str,
) -> tuple[list[dict[str, Any]], WHOFieldMappingReport]:
    mapped_fields: dict[str, str] = {}
    warnings: list[str] = []
    rows: list[dict[str, Any]] = []
    for index, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            continue
        row, field_map, row_warnings = map_who_row_to_review_row(
            record,
            query_request=query_request,
            source_id=source_id,
            row_index=index,
            material_type=material_type,
        )
        mapped_fields.update(field_map)
        warnings.extend(row_warnings)
        if row is not None:
            rows.append(row)
    if not rows:
        warnings.append("no_review_rows_mapped")
    return rows, WHOFieldMappingReport(source_id=source_id, mapped_fields=mapped_fields, warnings=sorted(set(warnings)))


def map_who_row_to_review_row(
    record: dict[str, Any],
    *,
    query_request: DiseaseTimeQueryRequest,
    source_id: str,
    row_index: int,
    material_type: str,
) -> tuple[dict[str, Any] | None, dict[str, str], list[str]]:
    mapped: dict[str, Any] = {}
    field_map: dict[str, str] = {}
    warnings: list[str] = []
    for raw_key, raw_value in record.items():
        canonical = normalize_who_field_name(str(raw_key))
        if canonical is None:
            continue
        field_map[str(raw_key)] = canonical
        mapped.setdefault(canonical, raw_value)
    if not any(field in mapped for field in ("confirmed_cases", "confirmed_deaths", "suspected_cases", "contacts_under_monitoring")):
        return None, field_map, ["missing_numeric_signal"]
    event_date = _normalize_date(mapped.get("date"), query_request)
    if event_date is None:
        event_date = query_request.date_start
        date_source = "report_date_fallback"
        confidence = 0.56
        warnings.append("date_fallback_used")
    else:
        date_source = "explicit_table_field"
        confidence = 0.74
    region = _text(mapped.get("region")) or query_request.country_or_region
    numeric_values = {
        "confirmed_cases": _int_or_none(mapped.get("confirmed_cases")),
        "confirmed_deaths": _int_or_none(mapped.get("confirmed_deaths")),
        "suspected_cases": _int_or_none(mapped.get("suspected_cases")),
        "contacts_under_monitoring": _int_or_none(mapped.get("contacts_under_monitoring")),
    }
    return (
        {
            "row_id": f"{source_id}-ROW{row_index}",
            "date": event_date,
            "country": _text(mapped.get("country")) or query_request.country_or_region,
            "region": region,
            "source_id": source_id,
            "evidence_summary": (
                f"WHO-first {material_type} row mapped from safe preview fields; "
                "requires human review before database ingestion."
            ),
            "confidence": confidence,
            "uncertainty_flags": sorted(set(["who_first_preview", material_type, *warnings, "human_review_required"])),
            "date_source": date_source,
            "date_anchor_id": f"{source_id}:date:{row_index}",
            "metric_anchor_id": f"{source_id}:metric:{row_index}",
            "date_confidence": "medium" if date_source == "explicit_table_field" else "low",
            "metric_source": material_type,
            "requires_human_review": True,
            "approval_status": "pending_review",
            **numeric_values,
        },
        field_map,
        warnings,
    )


def source_evidence_from_candidate(
    candidate: dict[str, Any],
    *,
    source_id: str,
    table_preview_rows: list[dict[str, Any]] | None = None,
    material_strength: str | None = None,
) -> SourceEvidenceItem:
    preview_rows = [
        {str(key)[:80]: str(value)[:120] for key, value in row.items()}
        for row in list(table_preview_rows or [])[:10]
        if isinstance(row, dict)
    ]
    headers = list(preview_rows[0])[:20] if preview_rows else []
    source_type = str(candidate.get("source_type") or candidate.get("recommended_parser_strategy") or "official_report_page")
    strength = material_strength or (
        "csv_preview"
        if source_type == "csv_dataset"
        else "json_preview"
        if source_type == "json_api"
        else "table_preview"
        if source_type == "html_table"
        else "document_candidate_summary"
    )
    dated = _signal_count(candidate, "date")
    numeric = _signal_count(candidate, "numeric")
    return SourceEvidenceItem(
        source_id=source_id,
        title_summary=str(candidate.get("title_summary") or candidate.get("title") or source_id)[:220],
        url_safe_summary=str(candidate.get("url_safe_summary") or candidate.get("url") or "")[:260],
        domain=str(candidate.get("domain") or "")[:180],
        published_date_hint=candidate.get("date_hint") or candidate.get("published_date_hint"),
        evidence_summary=str(candidate.get("evidence_summary") or candidate.get("snippet") or "")[:500],
        source_type=source_type,
        confidence=float(candidate.get("score") or candidate.get("disease_relevance_score") or 0.6),
        material_strength=strength,
        has_table_preview=bool(preview_rows and source_type != "json_api"),
        has_csv_preview=source_type == "csv_dataset",
        has_json_preview=source_type == "json_api",
        table_headers=headers,
        table_preview_rows=preview_rows,
        table_preview_rows_count=len(preview_rows),
        paragraph_signal_preview_count=1 if source_type in {"official_report_page", "pdf_situation_report_preview"} else 0,
        dated_numeric_signal_count=1 if preview_rows else 0,
        rows_with_date_and_numeric_hint=len(preview_rows),
        situation_report_hint=source_type in {"official_report_page", "pdf_situation_report_preview"},
        document_candidate=source_type in {"official_report_page", "pdf_situation_report_preview"},
        dated_signal_count=dated or (1 if preview_rows else 0),
        numeric_signal_count=numeric or (1 if preview_rows else 0),
        date_or_week_terms=["2025"] if dated else [],
        numeric_terms=["cases", "deaths"] if numeric else [],
        extraction_readiness="ready_for_table_extraction" if preview_rows else "needs_metadata_check",
        extraction_blockers=[] if preview_rows else ["needs_structured_preview"],
        recommended_next_action="human_review_rows" if preview_rows else "run_metadata_check_or_preview",
    )


def write_who_review_bundle(
    *,
    output_dir: str | Path,
    query_request: DiseaseTimeQueryRequest,
    sources: list[SourceEvidenceItem],
    extracted_rows: list[dict[str, Any]],
    warnings: list[str] | None = None,
):
    cleaned = clean_extracted_table_rows(extracted_rows, query_request)
    validated, issues = validate_extracted_table_rows(cleaned, query_request, sources)
    duplicates = find_duplicate_event_rows(validated)
    return write_structured_table_review(
        output_dir=output_dir,
        query_request=query_request,
        sources=sources,
        extracted_rows=extracted_rows,
        cleaned_rows=validated,
        validation_issues=issues,
        duplicate_groups=duplicates,
        deepseek_called=False,
        warnings=warnings or [],
        source_pack_readiness=source_pack_readiness(sources),
        deterministic_rows_generated=len(extracted_rows),
        deepseek_rows_generated=0,
        extraction_mode="who_first_downloadable_tabular_preview",
    )


def _json_records(value: Any, *, records_path: str | None) -> list[dict[str, Any]]:
    current = value
    if records_path:
        for part in records_path.split("."):
            if isinstance(current, dict):
                current = current.get(part)
            else:
                current = None
                break
    if isinstance(current, dict):
        for key in ("records", "rows", "data", "results", "items"):
            if isinstance(current.get(key), list):
                current = current[key]
                break
    if isinstance(current, list):
        return [item for item in current if isinstance(item, dict)]
    return []


def _normalize_date(value: Any, request: DiseaseTimeQueryRequest) -> str | None:
    if value in (None, ""):
        return None
    direct = normalize_evidence_date(str(value), request.date_start)
    if direct:
        return direct
    try:
        return date.fromisoformat(str(value)[:10]).isoformat()
    except ValueError:
        return None


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    text = re.sub(r"[^\d-]", "", str(value))
    if text in {"", "-"}:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _text(value: Any) -> str | None:
    text = " ".join(str(value or "").split())
    return text or None


def _signal_count(candidate: dict[str, Any], signal_type: str) -> int:
    text = " ".join(
        str(candidate.get(key) or "")
        for key in ("title_summary", "title", "evidence_summary", "snippet", "url_safe_summary", "url")
    ).lower()
    if signal_type == "date":
        return int(bool(re.search(r"\b20\d{2}\b|\bweek\b|\bdate\b|\breport\b", text)))
    return int(bool(re.search(r"\bcase|death|confirmed|suspected|contact|fatal", text)))


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._in_cell = False
        self._cell_text: list[str] = []
        self._current_row: list[str] = []
        self._rows: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        _ = attrs
        if tag.lower() in {"td", "th"}:
            self._in_cell = True
            self._cell_text = []

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._cell_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"td", "th"}:
            self._current_row.append(" ".join(" ".join(self._cell_text).split()))
            self._in_cell = False
        elif tag == "tr" and self._current_row:
            self._rows.append(self._current_row)
            self._current_row = []

    def records(self) -> list[dict[str, str]]:
        if len(self._rows) < 2:
            return []
        headers = [header or f"column_{index}" for index, header in enumerate(self._rows[0], start=1)]
        records: list[dict[str, str]] = []
        for row in self._rows[1:]:
            records.append({headers[index]: value for index, value in enumerate(row[: len(headers)])})
        return records


__all__ = [
    "WHOFieldMappingReport",
    "map_who_row_to_review_row",
    "normalize_who_field_name",
    "rows_from_csv_preview",
    "rows_from_html_table",
    "rows_from_json_payload",
    "rows_from_table_records",
    "source_evidence_from_candidate",
    "write_who_review_bundle",
]
