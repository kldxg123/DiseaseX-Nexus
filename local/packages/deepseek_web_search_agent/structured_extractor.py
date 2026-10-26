from __future__ import annotations

import re
from datetime import date
from typing import Any

from cleaner_agent.date_normalization import iso_week_from_date

from deepseek_web_search_agent.deepseek_client import DeepSeekClient
from deepseek_web_search_agent.result_summarizer import summarize_ranked_result
from deepseek_web_search_agent.types import (
    DeepSeekWebSearchRequest,
    ExtractedInformationRecord,
    PageReadSummary,
    RankedSearchResult,
)


def extract_information_records(
    ranked_results: list[RankedSearchResult],
    *,
    request: DeepSeekWebSearchRequest,
    page_summaries: list[PageReadSummary] | None = None,
    client: DeepSeekClient | None = None,
) -> tuple[list[ExtractedInformationRecord], dict[str, Any]]:
    page_by_url = {page.url: page for page in (page_summaries or [])}
    fallback_records = [
        deterministic_record_from_result(ranked, request=request, page_summary=page_by_url.get(ranked.result.url))
        for ranked in ranked_results[: request.max_search_results_per_query]
    ]
    if not request.allow_deepseek_api:
        return fallback_records, {"deepseek_call_count": 0, "warnings": ["deepseek_extraction_skipped_not_authorized"]}
    client = client or DeepSeekClient()
    response = client.call_with_search_context(
        task="extract_disease_country_records",
        context={
            "request": {
                "disease": request.disease,
                "country": request.country,
                "query_intent": request.query_intent,
            },
            "safe_candidates": [
                summarize_ranked_result(ranked, page_by_url.get(ranked.result.url))
                | {
                    "source_url": ranked.result.url,
                    "source_domain": ranked.result.domain,
                    "source_title": ranked.result.title,
                }
                for ranked in ranked_results[: request.max_search_results_per_query]
            ],
        },
        allow_deepseek_api=request.allow_deepseek_api,
        fallback_json={"records": [record.to_dict() for record in fallback_records]},
    )
    parsed_records = _records_from_json(response.parsed_json, request=request)
    return parsed_records or fallback_records, {
        "deepseek_call_count": 1 if response.called else 0,
        "warnings": response.warnings,
    }


def deterministic_record_from_result(
    ranked: RankedSearchResult,
    *,
    request: DeepSeekWebSearchRequest,
    page_summary: PageReadSummary | None = None,
) -> ExtractedInformationRecord:
    result = ranked.result
    text = " ".join(
        [
            result.title,
            result.snippet,
            page_summary.text_summary if page_summary and page_summary.text_summary else "",
        ]
    )
    event_date = _extract_date(text)
    iso_week = _extract_iso_week(text)
    if event_date and not iso_week:
        iso_week = iso_week_from_date(event_date)
    metric_name, metric_value = _extract_metric(text)
    information_type = _information_type(ranked, metric_value, event_date, iso_week)
    feature_ready = bool(metric_value is not None and (event_date or iso_week))
    return ExtractedInformationRecord(
        disease=request.disease.lower(),
        country=request.country,
        source_url=result.url,
        source_domain=result.domain,
        source_title=result.title[:240] if result.title else None,
        information_type=information_type,
        evidence_type="safe_search_summary",
        source_type=ranked.source_type,
        event_date=event_date,
        iso_week=iso_week,
        region_name=request.country,
        metric_name=metric_name,
        metric_value=metric_value,
        metric_unit="count" if metric_value is not None else None,
        text_summary=(page_summary.text_summary if page_summary and page_summary.text_summary else result.snippet)[:500],
        evidence_summary=_evidence_summary(text, metric_name, metric_value, event_date, iso_week),
        confidence=round(min(0.95, max(0.2, ranked.total_score)), 4),
        uncertainty_reason=None if feature_ready else "No explicit date/week and numeric metric were both found.",
        cleaning_hint="structured_event_candidate" if feature_ready else "evidence_only",
        feature_ready_potential=feature_ready,
        requires_human_review=True,
    )


def _records_from_json(payload: dict[str, Any], *, request: DeepSeekWebSearchRequest) -> list[ExtractedInformationRecord]:
    rows = payload.get("records")
    if not isinstance(rows, list):
        return []
    records: list[ExtractedInformationRecord] = []
    for item in rows:
        if not isinstance(item, dict) or not item.get("source_url"):
            continue
        event_date = _parse_date(item.get("event_date"))
        records.append(
            ExtractedInformationRecord(
                disease=str(item.get("disease") or request.disease).lower(),
                country=str(item.get("country") or request.country),
                source_url=str(item["source_url"]),
                source_domain=str(item.get("source_domain") or ""),
                source_title=item.get("source_title"),
                information_type=str(item.get("information_type") or "unknown"),
                evidence_type=str(item.get("evidence_type") or "safe_deepseek_json"),
                source_type=item.get("source_type"),
                event_date=event_date,
                iso_week=item.get("iso_week") if item.get("iso_week") else (iso_week_from_date(event_date) if event_date else None),
                region_name=item.get("region_name"),
                metric_name=item.get("metric_name"),
                metric_value=_float_or_none(item.get("metric_value")),
                metric_unit=item.get("metric_unit"),
                text_summary=item.get("text_summary"),
                evidence_summary=item.get("evidence_summary"),
                confidence=float(item.get("confidence") or 0.5),
                uncertainty_reason=item.get("uncertainty_reason"),
                cleaning_hint=item.get("cleaning_hint"),
                feature_ready_potential=bool(item.get("feature_ready_potential", False)),
                requires_human_review=True,
            )
        )
    return records


def _information_type(ranked: RankedSearchResult, metric_value: float | None, event_date: date | None, iso_week: str | None) -> str:
    if metric_value is not None and (event_date or iso_week):
        return "surveillance_metric"
    if ranked.source_type in {"csv_dataset", "json_api", "html_table", "structured_dataset_candidate"}:
        return "structured_dataset_candidate"
    if ranked.source_type == "dashboard_candidate":
        return "dashboard_candidate"
    if ranked.source_type == "official_report_evidence":
        return "official_report_evidence"
    return "disease_country_summary"


def _extract_metric(text: str) -> tuple[str | None, float | None]:
    lowered = text.lower()
    match = re.search(r"(\d+(?:\.\d+)?)\s+(?:confirmed\s+)?cases?\b", lowered)
    if match:
        return "cases", float(match.group(1))
    match = re.search(r"(\d+(?:\.\d+)?)\s+outbreaks?\b", lowered)
    if match:
        return "outbreaks_count", float(match.group(1))
    return None, None


def _extract_date(text: str) -> date | None:
    match = re.search(r"\b(20\d{2})-(\d{2})-(\d{2})\b", text)
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def _extract_iso_week(text: str) -> str | None:
    match = re.search(r"\b(20\d{2})-W(\d{1,2})\b", text, flags=re.I)
    if not match:
        return None
    week = int(match.group(2))
    if 1 <= week <= 53:
        return f"{match.group(1)}-W{week:02d}"
    return None


def _evidence_summary(
    text: str,
    metric_name: str | None,
    metric_value: float | None,
    event_date: date | None,
    iso_week: str | None,
) -> str:
    if metric_name and metric_value is not None and (event_date or iso_week):
        return f"Found {metric_name}={metric_value:g} with time reference {event_date or iso_week} in safe summary."
    return "Safe summary was retained as evidence; no model-ready numeric event was inferred."


def _parse_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
