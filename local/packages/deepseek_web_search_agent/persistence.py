from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from common.models import (
    DeepSeekWebSearchQuery,
    DeepSeekWebSearchResult,
    DeepSeekWebSearchRun,
    DiseaseCountryInformationRecord,
)
from deepseek_web_search_agent.types import (
    DeepSeekWebSearchRequest,
    ExtractedInformationRecord,
    QueryPlan,
    RankedSearchResult,
)


def create_or_update_run(
    session: Session,
    *,
    run_id: str,
    request: DeepSeekWebSearchRequest,
    search_backend: str,
    status: str,
    deepseek_model: str | None,
    warnings: list[str] | None = None,
    safety_summary: dict[str, Any] | None = None,
) -> DeepSeekWebSearchRun:
    row = session.scalar(select(DeepSeekWebSearchRun).where(DeepSeekWebSearchRun.run_id == run_id))
    if row is None:
        row = DeepSeekWebSearchRun(
            run_id=run_id,
            disease=request.disease.lower(),
            country=request.country,
            query_intent=request.query_intent,
            search_backend=search_backend,
            deepseek_model=deepseek_model,
            status=status,
            allow_deepseek_api=request.allow_deepseek_api,
            allow_general_web_search=request.allow_general_web_search,
            allow_page_read=request.allow_page_read,
            search_query_count=0,
            search_request_count=0,
            page_read_request_count=0,
            deepseek_call_count=0,
            records_extracted=0,
            records_persisted=0,
            warnings_json=list(warnings or []),
            safety_summary_json=dict(safety_summary or {}),
        )
        session.add(row)
    else:
        row.status = status
        row.warnings_json = list(warnings or [])
        row.safety_summary_json = dict(safety_summary or {})
    session.flush()
    return row


def persist_query_plan(session: Session, *, run_id: str, plan: QueryPlan) -> list[DeepSeekWebSearchQuery]:
    rows: list[DeepSeekWebSearchQuery] = []
    for query in plan.search_queries:
        row = DeepSeekWebSearchQuery(
            run_id=run_id,
            query_text=query.query_text,
            query_rank=query.query_rank,
            query_intent=query.query_intent,
            generated_by=query.generated_by,
        )
        session.add(row)
        rows.append(row)
    session.flush()
    return rows


def persist_ranked_results(
    session: Session,
    *,
    run_id: str,
    ranked_results: list[RankedSearchResult],
) -> list[DeepSeekWebSearchResult]:
    rows: list[DeepSeekWebSearchResult] = []
    for index, ranked in enumerate(ranked_results, start=1):
        result = ranked.result
        row = DeepSeekWebSearchResult(
            run_id=run_id,
            query_id=None,
            provider=result.provider,
            rank=index,
            title_summary=_truncate(result.title, 500),
            url=result.url,
            domain=result.domain,
            snippet_summary=_truncate(result.snippet, 500),
            published_date=result.published_date,
            source_type_hint=ranked.source_type,
            relevance_score=ranked.disease_relevance,
            structured_data_score=ranked.structured_time_series_likelihood,
            total_score=ranked.total_score,
            selected_for_page_read=ranked.selected_for_page_read,
        )
        session.add(row)
        rows.append(row)
    session.flush()
    return rows


def persist_information_records(
    session: Session,
    *,
    run_id: str,
    records: list[ExtractedInformationRecord],
) -> list[DiseaseCountryInformationRecord]:
    rows: list[DiseaseCountryInformationRecord] = []
    for record in records:
        row = DiseaseCountryInformationRecord(
            run_id=run_id,
            disease=record.disease.lower(),
            country=record.country,
            source_url=record.source_url,
            source_domain=record.source_domain,
            source_title=_truncate(record.source_title, 500),
            information_type=record.information_type,
            evidence_type=record.evidence_type,
            source_type=record.source_type,
            event_date=record.event_date,
            iso_week=record.iso_week,
            region_name=record.region_name,
            metric_name=record.metric_name,
            metric_value=record.metric_value,
            metric_unit=record.metric_unit,
            text_summary=_truncate(record.text_summary, 1000),
            evidence_summary=_truncate(record.evidence_summary, 1000),
            confidence=record.confidence,
            uncertainty_reason=_truncate(record.uncertainty_reason, 1000),
            cleaning_status="not_sent",
            raw_document_id=None,
            epidemic_event_id=None,
            feature_ready_potential=record.feature_ready_potential,
            requires_human_review=True,
        )
        session.add(row)
        rows.append(row)
    session.flush()
    return rows


def finalize_run_counts(
    session: Session,
    *,
    run_id: str,
    status: str,
    search_query_count: int,
    search_request_count: int,
    page_read_request_count: int,
    deepseek_call_count: int,
    records_extracted: int,
    records_persisted: int,
    warnings: list[str] | None = None,
    safety_summary: dict[str, Any] | None = None,
) -> None:
    row = session.scalar(select(DeepSeekWebSearchRun).where(DeepSeekWebSearchRun.run_id == run_id))
    if row is None:
        return
    row.status = status
    row.search_query_count = int(search_query_count)
    row.search_request_count = int(search_request_count)
    row.page_read_request_count = int(page_read_request_count)
    row.deepseek_call_count = int(deepseek_call_count)
    row.records_extracted = int(records_extracted)
    row.records_persisted = int(records_persisted)
    row.completed_at = datetime.now(UTC)
    row.warnings_json = list(warnings or [])
    row.safety_summary_json = dict(safety_summary or {})
    session.flush()


def list_runs(session: Session, *, limit: int = 50) -> list[dict]:
    rows = session.scalars(
        select(DeepSeekWebSearchRun).order_by(DeepSeekWebSearchRun.created_at.desc()).limit(limit)
    ).all()
    return [
        {
            "run_id": row.run_id,
            "disease": row.disease,
            "country": row.country,
            "query_intent": row.query_intent,
            "search_backend": row.search_backend,
            "status": row.status,
            "records_persisted": row.records_persisted,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "warnings": row.warnings_json,
        }
        for row in rows
    ]


def list_records(
    session: Session,
    *,
    disease: str | None = None,
    country: str | None = None,
    run_id: str | None = None,
    limit: int = 100,
) -> list[dict]:
    stmt = select(DiseaseCountryInformationRecord)
    if disease:
        stmt = stmt.where(DiseaseCountryInformationRecord.disease == disease.lower())
    if country:
        stmt = stmt.where(DiseaseCountryInformationRecord.country == country)
    if run_id:
        stmt = stmt.where(DiseaseCountryInformationRecord.run_id == run_id)
    rows = session.scalars(stmt.order_by(DiseaseCountryInformationRecord.created_at.desc()).limit(limit)).all()
    return [record_to_safe_dict(row) for row in rows]


def record_to_safe_dict(row: DiseaseCountryInformationRecord) -> dict:
    return {
        "id": row.id,
        "run_id": row.run_id,
        "disease": row.disease,
        "country": row.country,
        "source_domain": row.source_domain,
        "source_title": row.source_title,
        "information_type": row.information_type,
        "source_type": row.source_type,
        "event_date": row.event_date.isoformat() if row.event_date else None,
        "iso_week": row.iso_week,
        "region_name": row.region_name,
        "metric_name": row.metric_name,
        "metric_value": row.metric_value,
        "metric_unit": row.metric_unit,
        "evidence_summary": row.evidence_summary,
        "confidence": row.confidence,
        "cleaning_status": row.cleaning_status,
        "feature_ready_potential": row.feature_ready_potential,
        "requires_human_review": row.requires_human_review,
        "raw_document_id": row.raw_document_id,
        "epidemic_event_id": row.epidemic_event_id,
    }


def _truncate(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[: limit - 3] + "..."
