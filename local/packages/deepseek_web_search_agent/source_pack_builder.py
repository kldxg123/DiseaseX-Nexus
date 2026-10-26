from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from deepseek_web_search_agent.structured_table_types import DiseaseTimeQueryRequest, SourceEvidenceItem

MATERIAL_STRENGTHS = {
    "search_result_only",
    "page_summary",
    "page_with_structured_hints",
    "table_preview",
    "csv_preview",
    "json_preview",
    "paragraph_numeric_preview",
    "situation_report_preview",
    "document_candidate_summary",
    "connector_document",
    "structured_dataset_candidate",
}

READY_FOR_TABLE_EXTRACTION = "ready_for_table_extraction"
WEAK_BUT_USABLE = "weak_but_usable"
EVIDENCE_ONLY = "evidence_only"
INSUFFICIENT_FOR_TABLE_EXTRACTION = "insufficient_for_table_extraction"
NEEDS_METADATA_CHECK = "needs_metadata_check"
NEEDS_STRUCTURED_SOURCE = "needs_structured_source"


def build_source_pack(
    request: DiseaseTimeQueryRequest,
    *,
    source_pack_path: str | None = None,
    report_dir: str | None = None,
) -> list[SourceEvidenceItem]:
    if source_pack_path:
        loaded = load_source_pack(source_pack_path)
        if loaded:
            return _select_best_sources(loaded, request.max_sources)
    if report_dir:
        loaded = source_pack_from_report_dir(report_dir)
        if loaded:
            return _select_best_sources(loaded, request.max_sources)
    return mock_source_pack(request)


def load_source_pack(path: str | Path) -> list[SourceEvidenceItem]:
    payload = _read_json(Path(path))
    raw_sources = payload.get("sources") if isinstance(payload.get("sources"), list) else payload.get("source_pack")
    if not isinstance(raw_sources, list):
        for key in (
            "page_reads",
            "refined_review_queue",
            "review_queue",
            "metadata_results",
            "candidate_acquisitions",
            "raw_documents",
            "ranked_candidates",
            "candidates",
            "results",
            "records",
        ):
            if isinstance(payload.get(key), list):
                raw_sources = payload[key]
                break
    if not isinstance(raw_sources, list):
        raw_sources = payload if isinstance(payload, list) else []
    return [_source_from_mapping(item, index) for index, item in enumerate(raw_sources, start=1) if isinstance(item, dict)]


def source_pack_from_report_dir(report_dir: str | Path) -> list[SourceEvidenceItem]:
    root = Path(report_dir)
    for name in (
        "structured_source_pack.json",
        "source_pack.json",
        "source_map.json",
        "page_read_source_pack.json",
        "page_read_summary.json",
        "metadata_check_results.json",
        "candidate_acquisition_summary.json",
        "raw_document_ingest_summary.json",
        "deduped_ranked_candidates.json",
        "review_queue.json",
        "search_results_summary.json",
    ):
        loaded = load_source_pack(root / name)
        if loaded:
            return loaded
    return []


def mock_source_pack(request: DiseaseTimeQueryRequest) -> list[SourceEvidenceItem]:
    accessed = datetime.now(UTC).isoformat()
    disease = request.disease
    region = request.country_or_region
    return [
        SourceEvidenceItem(
            source_id="SRC1",
            title_summary=f"Official situation update for {disease}",
            url_safe_summary=f"https://example.org/{disease.lower()}-situation-update",
            domain="example.org",
            published_date_hint=request.date_start,
            accessed_at=accessed,
            evidence_summary=(
                f"Mock official bulletin mentions {disease} events in {region}; "
                "figures are synthetic for local review only."
            ),
            source_type="official_report",
            confidence=0.82,
            material_strength="table_preview",
            has_table_preview=True,
            table_headers=["date", "region", "confirmed_cases", "confirmed_deaths"],
            table_preview_rows=[
                {
                    "date": request.date_start,
                    "region": region,
                    "confirmed_cases": "12",
                    "confirmed_deaths": "3",
                },
            ],
            table_preview_rows_count=1,
            dated_numeric_signal_count=1,
            rows_with_date_and_numeric_hint=1,
            dated_signal_count=1,
            numeric_signal_count=1,
            extraction_readiness=READY_FOR_TABLE_EXTRACTION,
            recommended_next_action="run_deterministic_table_parser",
        ),
        SourceEvidenceItem(
            source_id="SRC2",
            title_summary=f"Regional surveillance table for {disease}",
            url_safe_summary=f"https://data.example.org/{disease.lower()}-surveillance-table",
            domain="data.example.org",
            published_date_hint=request.date_start,
            accessed_at=accessed,
            evidence_summary=(
                f"Mock surveillance table contains dated rows for {disease} in {region}; "
                "human approval is required before database ingestion."
            ),
            source_type="structured_table",
            confidence=0.88,
            material_strength="table_preview",
            has_table_preview=True,
            table_headers=["date", "region", "confirmed_cases", "confirmed_deaths"],
            table_preview_rows=[
                {
                    "date": request.date_end,
                    "region": region,
                    "confirmed_cases": "16",
                    "confirmed_deaths": "4",
                },
            ],
            table_preview_rows_count=1,
            dated_numeric_signal_count=1,
            rows_with_date_and_numeric_hint=1,
            dated_signal_count=1,
            numeric_signal_count=1,
            extraction_readiness=READY_FOR_TABLE_EXTRACTION,
            recommended_next_action="run_deterministic_table_parser",
        ),
    ][: request.max_sources]


def source_pack_to_dict(sources: list[SourceEvidenceItem]) -> dict[str, Any]:
    return {"sources": [source.to_dict() for source in sources]}


def _select_best_sources(sources: list[SourceEvidenceItem], limit: int) -> list[SourceEvidenceItem]:
    capped = max(1, int(limit or 1))
    return sorted(
        sources,
        key=lambda source: (
            _readiness_rank(source.extraction_readiness),
            _strength_rank(source.material_strength),
            source.dated_numeric_signal_count,
            source.paragraph_signal_preview_count,
            int(source.has_table_preview),
            int(source.has_csv_preview),
            source.numeric_signal_count,
            source.dated_signal_count,
            source.confidence,
        ),
        reverse=True,
    )[:capped]


def _readiness_rank(value: str) -> int:
    return {
        READY_FOR_TABLE_EXTRACTION: 5,
        WEAK_BUT_USABLE: 4,
        NEEDS_METADATA_CHECK: 3,
        NEEDS_STRUCTURED_SOURCE: 2,
        EVIDENCE_ONLY: 1,
        INSUFFICIENT_FOR_TABLE_EXTRACTION: 0,
    }.get(str(value or ""), 0)


def _strength_rank(value: str) -> int:
    return {
        "table_preview": 8,
        "csv_preview": 8,
        "paragraph_numeric_preview": 7,
        "situation_report_preview": 7,
        "json_preview": 5,
        "structured_dataset_candidate": 4,
        "connector_document": 4,
        "document_candidate_summary": 3,
        "page_with_structured_hints": 3,
        "page_summary": 2,
        "search_result_only": 1,
    }.get(str(value or ""), 0)


def source_pack_readiness(sources: list[SourceEvidenceItem]) -> dict[str, Any]:
    strengths: dict[str, int] = {}
    readiness: dict[str, int] = {}
    blockers: dict[str, int] = {}
    for source in sources:
        strengths[source.material_strength] = strengths.get(source.material_strength, 0) + 1
        readiness[source.extraction_readiness] = readiness.get(source.extraction_readiness, 0) + 1
        for blocker in source.extraction_blockers:
            blockers[blocker] = blockers.get(blocker, 0) + 1
    all_search_only = bool(sources) and all(source.material_strength == "search_result_only" for source in sources)
    has_preview = any(source.has_table_preview or source.has_csv_preview or source.has_json_preview for source in sources)
    has_extractable_preview = any(source.has_table_preview or source.has_csv_preview for source in sources)
    paragraph_signal_count = sum(source.paragraph_signal_preview_count for source in sources)
    dated_numeric_signal_count = sum(source.dated_numeric_signal_count for source in sources)
    document_candidate_count = sum(1 for source in sources if source.document_candidate)
    table_preview_count = sum(1 for source in sources if source.has_table_preview)
    dated = sum(source.dated_signal_count for source in sources)
    numeric = sum(source.numeric_signal_count for source in sources)
    level_blockers: list[str] = []
    if not sources:
        level_blockers.append("empty_source_pack")
    if all_search_only:
        level_blockers.append("search_result_only_material")
    if dated <= 0:
        level_blockers.append("no_dated_signal")
    if numeric <= 0:
        level_blockers.append("no_numeric_signal")
    if not (has_preview or paragraph_signal_count > 0):
        level_blockers.append("no_table_csv_json_preview")
    if has_preview and not (has_extractable_preview or paragraph_signal_count > 0 or dated_numeric_signal_count > 0):
        level_blockers.append("api_hint_without_data")
    if dated_numeric_signal_count <= 0:
        if sources and any(source.numeric_signal_count > 0 for source in sources):
            level_blockers.append("numeric_signal_without_date")
        if sources and any(source.dated_signal_count > 0 for source in sources):
            level_blockers.append("date_signal_without_numeric")
        if document_candidate_count:
            level_blockers.append("document_candidate_not_downloaded")
        level_blockers.append("needs_structured_document_or_dataset")
    return {
        "source_count": len(sources),
        "material_strength_counts": strengths,
        "extraction_readiness_counts": readiness,
        "blocker_counts": blockers,
        "source_pack_level_blockers": level_blockers,
        "has_table_csv_json_preview": has_preview,
        "has_structured_numeric_material": bool(has_extractable_preview or paragraph_signal_count > 0 or dated_numeric_signal_count > 0),
        "dated_signal_count": dated,
        "numeric_signal_count": numeric,
        "dated_numeric_signal_count": dated_numeric_signal_count,
        "paragraph_signal_preview_count": paragraph_signal_count,
        "table_preview_count": table_preview_count,
        "document_candidate_count": document_candidate_count,
        "ready_for_table_extraction": any(source.extraction_readiness == READY_FOR_TABLE_EXTRACTION for source in sources),
    }


def _source_from_mapping(item: dict[str, Any], index: int) -> SourceEvidenceItem:
    url = str(item.get("url_safe_summary") or item.get("url") or item.get("base_url") or "")
    domain = str(item.get("domain") or urlparse(url).netloc or "unknown")
    hints = item.get("hints") if isinstance(item.get("hints"), dict) else {}
    table_headers = _list_of_text(item.get("table_headers") or hints.get("table_headers"), limit=20)
    table_preview_rows = _preview_rows(item.get("table_preview_rows") or hints.get("table_preview_rows"))
    table_rows_count = int(item.get("table_preview_rows_count") or hints.get("table_preview_rows_count") or len(table_preview_rows))
    paragraph_signal_preview = _preview_rows(item.get("paragraph_signal_preview") or hints.get("paragraph_signal_preview"), row_limit=10)
    paragraph_signal_count = _int(
        item.get("paragraph_signal_preview_count")
        or hints.get("paragraph_signal_preview_count")
        or len(paragraph_signal_preview)
    )
    dated_numeric_signal_count = _int(item.get("dated_numeric_signal_count") or hints.get("dated_numeric_signal_count") or 0)
    rows_with_date_numeric = _int(
        item.get("rows_with_date_and_numeric_hint") or hints.get("rows_with_date_and_numeric_hint") or dated_numeric_signal_count
    )
    situation_report_hint = bool(item.get("situation_report_hint") or hints.get("situation_report_hint"))
    document_candidate = bool(
        item.get("document_candidate")
        or hints.get("document_download_hint")
        or str(item.get("source_type") or "").lower() == "document_candidate"
    )
    has_table_preview = bool(table_preview_rows or table_headers)
    has_csv_preview = bool(item.get("has_csv_preview") or hints.get("has_csv_hint") or hints.get("csv_preview_rows"))
    has_json_preview = bool(item.get("has_json_preview") or hints.get("has_json_api_hint") or hints.get("json_preview"))
    dated_count = _int(
        item.get("dated_signal_count")
        or item.get("date_terms_count")
        or hints.get("date_terms_count")
        or hints.get("week_terms_count")
        or 0
    )
    numeric_count = _int(item.get("numeric_signal_count") or item.get("numeric_terms_count") or hints.get("numeric_terms_count") or 0)
    date_terms = _list_of_text(item.get("date_or_week_terms") or hints.get("date_or_week_terms"), limit=20)
    numeric_terms = _list_of_text(item.get("numeric_terms") or hints.get("numeric_terms"), limit=20)
    material_strength = _material_strength(
        item,
        hints,
        has_table_preview,
        has_csv_preview,
        has_json_preview,
        paragraph_signal_count=paragraph_signal_count,
        situation_report_hint=situation_report_hint,
        document_candidate=document_candidate,
    )
    readiness, blockers, next_action = _readiness(
        material_strength=material_strength,
        has_table_preview=has_table_preview,
        has_csv_preview=has_csv_preview,
        has_json_preview=has_json_preview,
        paragraph_signal_count=paragraph_signal_count,
        dated_numeric_signal_count=dated_numeric_signal_count,
        document_candidate=document_candidate,
        dated_signal_count=dated_count,
        numeric_signal_count=numeric_count,
    )
    reason = "ready" if readiness == READY_FOR_TABLE_EXTRACTION else "; ".join(blockers) or readiness
    return SourceEvidenceItem(
        source_id=str(item.get("source_id") or f"SRC{index}"),
        title_summary=str(item.get("title_summary") or item.get("title") or item.get("name") or f"Source {index}")[:220],
        url_safe_summary=url[:260],
        domain=domain[:180],
        published_date_hint=item.get("published_date_hint") or item.get("published_at") or item.get("date_hint"),
        accessed_at=str(item.get("accessed_at") or datetime.now(UTC).isoformat()),
        evidence_summary=str(item.get("evidence_summary") or item.get("summary") or item.get("snippet") or item.get("text_summary") or "")[:500],
        source_type=str(item.get("source_type") or "evidence_summary"),
        confidence=_float(item.get("confidence"), default=0.5),
        full_text_saved=False,
        material_strength=material_strength,
        has_table_preview=has_table_preview,
        has_csv_preview=has_csv_preview,
        has_json_preview=has_json_preview,
        table_headers=table_headers,
        table_preview_rows=table_preview_rows,
        table_preview_rows_count=table_rows_count,
        paragraph_signal_preview=paragraph_signal_preview,
        paragraph_signal_preview_count=paragraph_signal_count,
        dated_numeric_signal_count=dated_numeric_signal_count,
        rows_with_date_and_numeric_hint=rows_with_date_numeric,
        situation_report_hint=situation_report_hint,
        document_candidate=document_candidate,
        dated_signal_count=dated_count,
        numeric_signal_count=numeric_count,
        date_or_week_terms=date_terms,
        numeric_terms=numeric_terms,
        extraction_readiness=readiness,
        extraction_blockers=blockers,
        extraction_readiness_reason=reason,
        why_not_ready=blockers if readiness != READY_FOR_TABLE_EXTRACTION else [],
        recommended_next_action=next_action,
    )


def _read_json(path: Path) -> Any:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _float(value: Any, *, default: float) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


def _int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _list_of_text(value: Any, *, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item)[:120] for item in value if str(item).strip()][:limit]


def _preview_rows(value: Any, *, row_limit: int = 5) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    rows: list[dict[str, str]] = []
    for row in value[:row_limit]:
        if not isinstance(row, dict):
            continue
        rows.append({str(key)[:80]: str(item)[:120] for key, item in row.items() if str(key).strip()})
    return rows


def _material_strength(
    item: dict[str, Any],
    hints: dict[str, Any],
    has_table_preview: bool,
    has_csv_preview: bool,
    has_json_preview: bool,
    paragraph_signal_count: int,
    situation_report_hint: bool,
    document_candidate: bool,
) -> str:
    explicit = str(item.get("material_strength") or "").strip()
    if explicit in MATERIAL_STRENGTHS:
        return explicit
    source_type = str(item.get("source_type") or "").lower()
    if "connector" in source_type or "raw_document" in source_type:
        return "connector_document"
    if has_csv_preview:
        return "csv_preview"
    if has_json_preview:
        return "json_preview"
    if has_table_preview:
        return "table_preview"
    if paragraph_signal_count > 0:
        return "situation_report_preview" if situation_report_hint else "paragraph_numeric_preview"
    if document_candidate:
        return "document_candidate_summary"
    if hints.get("has_table_hint") or hints.get("has_download_hint") or hints.get("has_dashboard_hint"):
        return "page_with_structured_hints"
    if item.get("text_summary"):
        return "page_summary"
    if item.get("parser_strategy") or item.get("connector_name") or item.get("structured_preview"):
        return "structured_dataset_candidate"
    return "search_result_only"


def _readiness(
    *,
    material_strength: str,
    has_table_preview: bool,
    has_csv_preview: bool,
    has_json_preview: bool,
    paragraph_signal_count: int,
    dated_numeric_signal_count: int,
    document_candidate: bool,
    dated_signal_count: int,
    numeric_signal_count: int,
) -> tuple[str, list[str], str]:
    blockers: list[str] = []
    if dated_numeric_signal_count <= 0:
        if numeric_signal_count > 0 and dated_signal_count <= 0:
            blockers.append("numeric_signal_without_date")
        if dated_signal_count > 0 and numeric_signal_count <= 0:
            blockers.append("date_signal_without_numeric")
    if dated_signal_count <= 0:
        blockers.append("no_dated_numeric_signal")
    if numeric_signal_count <= 0:
        blockers.append("no_numeric_signal")
    if not (has_table_preview or has_csv_preview or has_json_preview or paragraph_signal_count > 0):
        blockers.append("no_table_csv_json_preview")
    if has_json_preview and not (has_table_preview or paragraph_signal_count or dated_numeric_signal_count):
        blockers.append("api_hint_without_data")
    if document_candidate:
        blockers.append("document_candidate_not_downloaded")
    if material_strength == "search_result_only":
        blockers.append("insufficient_source_material")
        return NEEDS_STRUCTURED_SOURCE, blockers, "search_structured_time_series_source"
    has_extractable_material = bool(has_table_preview or has_csv_preview or paragraph_signal_count > 0 or dated_numeric_signal_count > 0)
    if has_extractable_material:
        if dated_signal_count > 0 and numeric_signal_count > 0 and (
            has_table_preview or has_csv_preview or dated_numeric_signal_count > 0
        ):
            return READY_FOR_TABLE_EXTRACTION, [], "run_deterministic_table_parser"
        if paragraph_signal_count > 0 and dated_numeric_signal_count <= 0:
            blockers.append("needs_structured_document_or_dataset")
        return WEAK_BUT_USABLE, blockers, "review_table_preview_or_run_metadata_check"
    if has_json_preview:
        blockers.append("needs_structured_document_or_dataset")
        return NEEDS_STRUCTURED_SOURCE, sorted(set(blockers)), "find_json_rows_or_structured_table_preview"
    if material_strength in {"structured_dataset_candidate", "page_with_structured_hints"}:
        return NEEDS_METADATA_CHECK, blockers, "run_metadata_check"
    if dated_signal_count > 0 or numeric_signal_count > 0:
        return WEAK_BUT_USABLE, blockers, "run_metadata_check"
    return EVIDENCE_ONLY, blockers, "keep_as_evidence_or_search_structured_source"
