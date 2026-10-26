from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

from deepseek_web_search_agent.deepseek_client import DeepSeekClient
from deepseek_web_search_agent.deterministic_table_extractor import deterministic_rows_from_sources
from deepseek_web_search_agent.contextual_evidence_builder import build_contextual_evidence_pack
from deepseek_web_search_agent.deepseek_diagnostics import run_deepseek_health_check
from deepseek_web_search_agent.deepseek_table_prompt import (
    build_structured_event_table_prompt,
    prompt_hash,
    prompt_summary,
)
from deepseek_web_search_agent.source_pack_builder import source_pack_readiness, source_pack_to_dict
from deepseek_web_search_agent.staging_review import write_structured_table_review
from deepseek_web_search_agent.table_cleaner import clean_extracted_table_rows
from deepseek_web_search_agent.table_deduper import find_duplicate_event_rows
from deepseek_web_search_agent.table_query_orchestrator import build_table_query_source_pack
from deepseek_web_search_agent.table_response_parser import parse_deepseek_table_response, parse_int_or_none
from deepseek_web_search_agent.table_validator import validate_extracted_table_rows, validation_summary


def run_structured_table_review(
    *,
    disease: str,
    country_or_region: str,
    date_start: str,
    date_end: str,
    output_dir: str = "reports/deepseek_table_review",
    source_pack_path: str | None = None,
    mock_deepseek: bool = True,
    allow_deepseek: bool = False,
    allow_web_search: bool = False,
    allow_page_read: bool = False,
    allow_live_source_search: bool = False,
    allow_network: bool = False,
    terms_reviewed: bool = False,
    manual_reviewed: bool = False,
    providers: str = "auto",
    max_total_provider_requests: int = 3,
    metadata_check_top_n: int = 3,
    max_metadata_check_requests: int = 3,
    max_sources: int = 3,
    deepseek_client: DeepSeekClient | None = None,
) -> dict[str, Any]:
    orchestration = build_table_query_source_pack(
        disease=disease,
        country_or_region=country_or_region,
        date_start=date_start,
        date_end=date_end,
        max_sources=max_sources,
        source_pack_path=source_pack_path,
        allow_live_source_search=allow_live_source_search,
        allow_network=allow_network,
        terms_reviewed=terms_reviewed,
        manual_reviewed=manual_reviewed,
        providers=providers,
        max_total_provider_requests=max_total_provider_requests,
        metadata_check_top_n=metadata_check_top_n,
        max_metadata_check_requests=max_metadata_check_requests,
        output_dir=output_dir,
    )
    request = orchestration.query_request
    run_dir = make_table_review_run_dir(output_dir, request.disease, request.country_or_region)
    sources = orchestration.sources
    contextual_pack = build_contextual_evidence_pack(sources, date_start=request.date_start, date_end=request.date_end)
    contextual_payload = contextual_pack.to_dict()
    prompt = build_structured_event_table_prompt(request, sources, contextual_payload)
    p_hash = prompt_hash(prompt)
    warnings: list[str] = list(orchestration.warnings)
    deepseek_called = False
    deterministic_rows = deterministic_rows_from_sources(sources, request)
    extraction_mode = "insufficient_material"
    deepseek_rows_generated = 0
    if deterministic_rows:
        response = {"rows": deterministic_rows, "extraction_warnings": []}
        extraction_mode = "deterministic_parser"
    elif mock_deepseek or not allow_deepseek:
        response = mock_table_extraction_response(request)
        extraction_mode = "deterministic_parser" if mock_deepseek else "insufficient_material"
        if not allow_deepseek:
            warnings.append("deepseek_not_called_without_explicit_allow")
    else:
        health = run_deepseek_health_check(allow_deepseek_api=True)
        if health.get("health_check_status") != "ok":
            response = {"rows": [], "extraction_warnings": ["deepseek_health_check_failed"]}
            warnings.append("deepseek_health_check_failed")
        else:
            client = deepseek_client or DeepSeekClient()
            call = client.call_chat_json(
                system_prompt="Extract candidate disease event rows and return strict JSON only.",
                user_payload={
                    "query": request.to_dict(),
                    "source_pack": source_pack_to_dict(sources),
                    "contextual_evidence_pack": contextual_payload,
                },
                allow_deepseek_api=True,
                fallback_json={"rows": [], "extraction_warnings": ["deepseek_fallback_empty"]},
                json_mode="strict_json",
            )
            response = call.parsed_json
            deepseek_called = call.called
            warnings.extend(call.warnings)
        extraction_mode = "deepseek_table_extraction"
    parsed = parse_deepseek_table_response(response)
    if extraction_mode == "deepseek_table_extraction":
        deepseek_rows_generated = len(parsed["rows"])
    warnings.extend(parsed.get("extraction_warnings") or [])
    cleaned = clean_extracted_table_rows(parsed["rows"], request)
    validated_rows, issues = validate_extracted_table_rows(cleaned, request, sources)
    duplicates = find_duplicate_event_rows(validated_rows)
    bundle = write_structured_table_review(
        output_dir=run_dir,
        query_request=request,
        sources=sources,
        extracted_rows=parsed["rows"],
        cleaned_rows=validated_rows,
        validation_issues=issues,
        duplicate_groups=duplicates,
        prompt_hash=p_hash,
        prompt_summary=prompt_summary(request, sources, contextual_payload),
        deepseek_called=deepseek_called,
        warnings=warnings,
        source_pack_readiness=source_pack_readiness(sources),
        deterministic_rows_generated=len(deterministic_rows),
        deepseek_rows_generated=deepseek_rows_generated,
        extraction_mode=extraction_mode,
    )
    summary = {
        "status": "ok",
        "run_id": run_dir.name,
        "run_dir": str(run_dir),
        "disease": request.disease,
        "country_or_region": request.country_or_region,
        "date_start": request.date_start,
        "date_end": request.date_end,
        "mock_deepseek": bool(mock_deepseek),
        "deepseek_called": deepseek_called,
        "source_count": len(sources),
        "network_accessed": orchestration.network_accessed,
        "provider_request_count_total": orchestration.provider_request_count_total,
        "candidate_acquisition_request_count": orchestration.candidate_acquisition_request_count,
        "source_search_summary": orchestration.source_search_summary,
        "mock_rows_generated": len(parsed["rows"]) if mock_deepseek else 0,
        "deterministic_rows_generated": len(deterministic_rows),
        "deepseek_rows_generated": deepseek_rows_generated,
        "extraction_mode": extraction_mode,
        "date_anchor_count": len(contextual_pack.date_anchors),
        "metric_anchor_count": len(contextual_pack.metric_anchors),
        "context_linked_candidate_count": len(contextual_pack.linked_candidates),
        "source_pack_readiness": source_pack_readiness(sources),
        "cleaned_rows": len(validated_rows),
        "validation": validation_summary(validated_rows, issues),
        "validation_issues_count": len(issues),
        "duplicate_groups_count": len(duplicates),
        "approval_ingestion_executed": False,
        "paths": bundle.report_paths,
        "warnings": warnings,
        "limitations": "Rows are staging candidates only. Human approval is required before database ingestion.",
    }
    return summary


def _deterministic_rows_from_sources(sources, request) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for source in sources:
        preview_rows = list(getattr(source, "table_preview_rows", []) or [])
        if not preview_rows:
            continue
        for preview in preview_rows[:5]:
            if not isinstance(preview, dict):
                continue
            row = _row_from_preview(preview, source, request, index=len(rows) + 1)
            if row is not None:
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
            date_value = _normalize_preview_date(value, request.date_start)
        elif region is None and any(term in key for term in ("state", "region", "jurisdiction", "location")):
            region = value[:120]
        if any(term in key for term in ("death", "fatal")):
            numeric_values["confirmed_deaths"] = parse_int_or_none(value)
        elif any(term in key for term in ("suspect", "probable")):
            numeric_values["suspected_cases"] = parse_int_or_none(value)
        elif "contact" in key:
            numeric_values["contacts_under_monitoring"] = parse_int_or_none(value)
        elif any(term in key for term in ("case", "positive", "count", "total")):
            numeric_values["confirmed_cases"] = parse_int_or_none(value)
    if not date_value or not any(value is not None for value in numeric_values.values()):
        return None
    return {
        "row_id": f"ROW{index}",
        "date": date_value,
        "country": request.country_or_region,
        "region": region or request.country_or_region,
        "source_id": source.source_id,
        "evidence_summary": f"Deterministic preview row from {source.source_id}; human review required.",
        "confidence": 0.68,
        "uncertainty_flags": ["table_preview_limited", "human_review_required"],
        **numeric_values,
    }


def _normalize_preview_date(value: str, fallback_start: str) -> str | None:
    text = str(value or "").strip()
    match = re.search(r"\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b", text)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3))).isoformat()
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


def mock_table_extraction_response(request) -> dict[str, Any]:
    start = request.date_start
    return {
        "rows": [
            {
                "date": start,
                "country": request.country_or_region if request.country_or_region != "African Region" else "South Africa",
                "region": "Gauteng Province",
                "confirmed_cases": 2,
                "confirmed_deaths": 1,
                "suspected_cases": 0,
                "contacts_under_monitoring": 62,
                "source_id": "SRC1",
                "evidence_summary": "Mock source says two confirmed cases and one death; human review required.",
                "confidence": 0.82,
                "uncertainty_flags": ["mock_extraction"],
            },
            {
                "date": start,
                "country": "Cabo Verde" if request.country_or_region == "African Region" else request.country_or_region,
                "region": "Praia offshore waters" if request.country_or_region == "African Region" else "Unspecified region",
                "confirmed_cases": 0,
                "confirmed_deaths": 0,
                "suspected_cases": 3,
                "contacts_under_monitoring": 0,
                "source_id": "SRC2",
                "evidence_summary": "Mock surveillance table mentions suspected cases; no automatic fact claim is made.",
                "confidence": 0.78,
                "uncertainty_flags": ["mock_extraction", "requires_source_confirmation"],
            },
        ],
        "source_notes": ["Mock DeepSeek response for local tests only."],
        "extraction_warnings": [],
    }


def make_table_review_run_dir(output_dir: str | Path, disease: str, country_or_region: str) -> Path:
    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    safe = "_".join(f"{disease}_{country_or_region}".lower().replace("/", " ").split())
    return Path(output_dir) / f"table_review_{safe}_{stamp}"
