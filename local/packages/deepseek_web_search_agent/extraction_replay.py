from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from deepseek_web_search_agent.cleaning_bridge import send_run_to_cleaning
from deepseek_web_search_agent.deepseek_client import DeepSeekClient
from deepseek_web_search_agent.deepseek_diagnostics import suggested_fix_for_failure
from deepseek_web_search_agent.persistence import (
    create_or_update_run,
    finalize_run_counts,
    persist_information_records,
    persist_ranked_results,
)
from deepseek_web_search_agent.structured_extractor import extract_information_records
from deepseek_web_search_agent.types import DeepSeekWebSearchRequest, GeneralWebSearchResult, RankedSearchResult
from deepseek_web_search_agent.visualization_data import build_3d_visualization_dataset


def load_external_search_live_run(run_dir: str | Path) -> dict[str, Any]:
    root = Path(run_dir)
    summary = _read_json(root / "external_search_live_summary.json")
    top_search_summary = _read_json(root / "search_results_summary.json")
    nested_search_summary = _read_nested_search_results(root)
    database_summary = _read_json(root / "database_persistence_summary.json")
    cleaning_summary = _read_json(root / "cleaning_bridge_summary.json")
    visualization = _read_json(root / "visualization_3d_data.json")
    return {
        "run_dir": str(root),
        "summary": summary,
        "search_results_summary": top_search_summary,
        "nested_search_results_summary": nested_search_summary,
        "database_summary": database_summary,
        "cleaning_summary": cleaning_summary,
        "visualization": visualization,
    }


def extract_safe_search_context_for_deepseek(run_dir: str | Path, *, limit: int = 8) -> dict[str, Any]:
    payload = load_external_search_live_run(run_dir)
    summary = payload.get("summary") or {}
    results = (payload.get("nested_search_results_summary") or {}).get("results") or []
    candidates = []
    for item in results[:limit]:
        if not isinstance(item, dict):
            continue
        candidates.append(
            {
                "title": _truncate(item.get("title"), 220),
                "url": str(item.get("url") or ""),
                "domain": str(item.get("domain") or ""),
                "snippet_summary": _truncate(item.get("snippet"), 320),
                "source_type": str(item.get("source_type") or "unknown"),
                "total_score": float(item.get("total_score") or 0.0),
                "query": _truncate(item.get("query"), 180),
            }
        )
    return {
        "disease": str(summary.get("disease") or ""),
        "country": str(summary.get("country") or ""),
        "source_run_id": summary.get("run_id"),
        "selected_provider": summary.get("selected_provider"),
        "external_search_provider_called": summary.get("external_search_provider_called"),
        "page_read_request_count": int(summary.get("page_read_request_count") or 0),
        "safe_candidates": candidates,
    }


def run_structured_extraction_replay(
    *,
    session: Session,
    run_dir: str | Path,
    allow_deepseek_api: bool,
    output_dir: str,
    health_check: dict[str, Any] | None = None,
) -> dict[str, Any]:
    context = extract_safe_search_context_for_deepseek(run_dir)
    disease = context["disease"] or "unknown"
    country = context["country"] or "unknown"
    replay_run_id = f"deepseek_replay_{_slug(disease)}_{_slug(country)}_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    request = DeepSeekWebSearchRequest(
        disease=disease,
        country=country,
        query_intent="structured_time_series",
        search_backend="replay_existing_search_summary",
        allow_deepseek_api=allow_deepseek_api,
        allow_general_web_search=False,
        allow_page_read=False,
        terms_reviewed=True,
        manual_reviewed=True,
        max_search_queries=1,
        max_search_results_per_query=min(8, len(context["safe_candidates"]) or 1),
        max_pages_to_read=0,
        max_total_http_requests=0,
        output_dir=output_dir,
    ).validated()
    ranked = _ranked_results_from_context(context)
    create_or_update_run(
        session,
        run_id=replay_run_id,
        request=request,
        search_backend="replay_existing_search_summary",
        status="running",
        deepseek_model=None,
        warnings=[],
        safety_summary={
            "no_tavily_rerun": True,
            "page_read_request_count": 0,
            "candidate_urls_opened": False,
            "full_model_input_saved": False,
            "full_model_output_saved": False,
        },
    )
    persist_ranked_results(session, run_id=replay_run_id, ranked_results=ranked)
    records, stats = extract_information_records(
        ranked,
        request=request,
        page_summaries=[],
        client=DeepSeekClient(),
    )
    fallback_used = bool(
        not allow_deepseek_api
        or any(
            warning
            in {
                "deepseek_api_http_error",
                "deepseek_api_timeout",
                "deepseek_api_failed",
                "deepseek_json_parse_error",
                "deepseek_api_skipped_missing_key",
                "deepseek_api_skipped_not_authorized",
            }
            for warning in stats.get("warnings", [])
        )
    )
    persisted = persist_information_records(session, run_id=replay_run_id, records=records)
    cleaning = send_run_to_cleaning(session, run_id=replay_run_id)
    visualization = build_3d_visualization_dataset(session, disease=disease, country=country)
    warnings = list(stats.get("warnings") or [])
    finalize_run_counts(
        session,
        run_id=replay_run_id,
        status="ok" if persisted else "no_records",
        search_query_count=0,
        search_request_count=0,
        page_read_request_count=0,
        deepseek_call_count=int(stats.get("deepseek_call_count") or 0),
        records_extracted=len(records),
        records_persisted=len(persisted),
        warnings=warnings,
        safety_summary={"no_tavily_rerun": True, "fallback_extraction_used": fallback_used},
    )
    failure_type = _failure_type_from_warnings(warnings)
    return {
        "status": "ok" if persisted else "no_records",
        "run_id": replay_run_id,
        "source_run_id": context.get("source_run_id"),
        "disease": disease,
        "country": country,
        "deepseek_call_count": int(stats.get("deepseek_call_count") or 0),
        "records_extracted_by_deepseek": 0 if fallback_used else len(records),
        "records_persisted": len(persisted),
        "raw_documents_created": cleaning.get("raw_documents_created", 0),
        "epidemic_events_created": cleaning.get("epidemic_events_created", 0),
        "feature_ready_records": cleaning.get("feature_ready_records", 0),
        "visualization_points": len(visualization.get("points") or []),
        "fallback_extraction_used": fallback_used,
        "failure_type": failure_type,
        "suggested_fix": suggested_fix_for_failure(failure_type) if failure_type else None,
        "warnings": warnings,
        "cleaning_summary": cleaning,
        "visualization_data": visualization,
        "health_check": health_check or {},
        "no_tavily_rerun": True,
        "page_read_request_count": 0,
    }


def _ranked_results_from_context(context: dict[str, Any]) -> list[RankedSearchResult]:
    rows = []
    for index, item in enumerate(context.get("safe_candidates") or [], start=1):
        result = GeneralWebSearchResult(
            query=str(item.get("query") or ""),
            rank=index,
            title=str(item.get("title") or ""),
            url=str(item.get("url") or ""),
            domain=str(item.get("domain") or ""),
            snippet=str(item.get("snippet_summary") or ""),
            provider=str(context.get("selected_provider") or "existing_search_summary"),
            source_type_hint=str(item.get("source_type") or "unknown"),
        )
        score = float(item.get("total_score") or 0.0)
        rows.append(
            RankedSearchResult(
                result=result,
                disease_relevance=score,
                country_relevance=score,
                authority=score,
                data_likelihood=score,
                structured_time_series_likelihood=score,
                recency=0.0,
                evidence_quality=score,
                safety_risk=0.0,
                total_score=score,
                source_type=str(item.get("source_type") or "unknown"),
                selected_for_page_read=False,
            )
        )
    return rows


def _read_nested_search_results(root: Path) -> dict[str, Any]:
    for path in root.glob("run_deepseek_*/search_results_summary.json"):
        payload = _read_json(path)
        if payload:
            return payload
    return {}


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _truncate(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _failure_type_from_warnings(warnings: list[str]) -> str | None:
    if "deepseek_api_http_error" in warnings:
        return "unknown_http_error"
    if "deepseek_api_timeout" in warnings:
        return "timeout"
    if "deepseek_api_failed" in warnings:
        return "unknown_http_error"
    if "deepseek_json_parse_error" in warnings:
        return "parse_error"
    if "deepseek_api_skipped_missing_key" in warnings:
        return "missing_api_key"
    if "deepseek_api_skipped_not_authorized" in warnings:
        return "not_authorized"
    return None


def _slug(value: str) -> str:
    return "_".join(part for part in str(value or "").lower().replace("/", " ").split() if part) or "run"
