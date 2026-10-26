from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from cleaner_agent.event_extraction import extract_event_from_raw_document
from common.models import DiseaseCountryInformationRecord, EpidemicEvent, RawDocument, SourceRegistry
from crawler_agent.raw_document_store import calculate_content_hash


def information_record_to_connector_document(record: DiseaseCountryInformationRecord) -> dict[str, Any]:
    body_lines = [
        f"Disease: {record.disease}",
        f"Country: {record.country}",
        f"Information type: {record.information_type}",
        f"Evidence: {record.evidence_summary or record.text_summary or 'safe summary unavailable'}",
    ]
    if record.metric_name and record.metric_value is not None:
        body_lines.append(f"{record.metric_name}: {record.metric_value:g} {record.metric_unit or ''}".strip())
    if record.event_date:
        body_lines.append(f"Event date: {record.event_date.isoformat()}")
    if record.iso_week:
        body_lines.append(f"ISO week: {record.iso_week}")
    return {
        "title": record.source_title or f"{record.disease} {record.country} evidence",
        "body_text": "\n".join(body_lines),
        "url": record.source_url,
        "content_type": "text/plain; charset=utf-8",
        "metadata_json": {
            "source": "deepseek_web_search_agent",
            "run_id": record.run_id,
            "information_record_id": record.id,
            "structured_event": _structured_event(record),
        },
    }


def persist_records_as_raw_documents(
    session: Session,
    *,
    run_id: str,
) -> dict[str, int]:
    records = session.scalars(
        select(DiseaseCountryInformationRecord).where(DiseaseCountryInformationRecord.run_id == run_id)
    ).all()
    created = duplicates = skipped = 0
    for record in records:
        if not _eligible_for_cleaning(record):
            record.cleaning_status = "evidence_only"
            skipped += 1
            continue
        source = _get_or_create_source(session, record)
        document_payload = information_record_to_connector_document(record)
        content_hash = calculate_content_hash(document_payload["title"], document_payload["body_text"])
        existing = session.scalar(select(RawDocument).where(RawDocument.content_hash == content_hash))
        if existing is not None:
            record.raw_document_id = existing.id
            record.cleaning_status = "raw_document_duplicate"
            duplicates += 1
            continue
        document = RawDocument(
            source_registry_id=source.id,
            original_url=record.source_url,
            normalized_url=record.source_url,
            title=document_payload["title"],
            body_text=document_payload["body_text"],
            content_hash=content_hash,
            status_code=200,
            content_type=document_payload["content_type"],
            country_detected=record.country,
            disease_detected=record.disease,
            extraction_status="pending",
            fetched_at=datetime.now(UTC),
            metadata_json=document_payload["metadata_json"],
        )
        session.add(document)
        session.flush()
        record.raw_document_id = document.id
        record.cleaning_status = "raw_document_created"
        created += 1
    session.flush()
    return {"raw_documents_created": created, "duplicates": duplicates, "skipped": skipped}


def run_cleaner_for_records(session: Session, *, run_id: str) -> dict[str, int]:
    records = session.scalars(
        select(DiseaseCountryInformationRecord).where(DiseaseCountryInformationRecord.run_id == run_id)
    ).all()
    created = skipped = duplicate = 0
    for record in records:
        if not record.raw_document_id:
            skipped += 1
            continue
        document = session.get(RawDocument, record.raw_document_id)
        if document is None:
            skipped += 1
            continue
        existing_event = session.scalar(select(EpidemicEvent).where(EpidemicEvent.raw_document_id == document.id))
        if existing_event is not None:
            record.epidemic_event_id = existing_event.id
            record.cleaning_status = "event_duplicate"
            duplicate += 1
            continue
        event = extract_event_from_raw_document(
            document,
            target_country=record.country,
            target_disease=record.disease,
        )
        if event is None:
            record.cleaning_status = "no_event"
            skipped += 1
            continue
        row = EpidemicEvent(
            raw_document_id=document.id,
            country=event.country,
            admin1=event.admin1,
            admin2=event.admin2,
            disease=event.disease,
            event_date=event.event_date,
            iso_week=event.iso_week,
            cases=event.cases,
            suspected_cases=event.suspected_cases,
            confirmed_cases=event.confirmed_cases,
            deaths=event.deaths,
            hospitalizations=event.hospitalizations,
            tests=event.tests,
            positivity_rate=event.positivity_rate,
            outbreaks_count=event.outbreaks_count,
            setting=event.setting,
            pathogen_genotype=event.pathogen_genotype,
            confidence_score=event.confidence_score,
            extraction_method=event.extraction_method,
            provenance_json={
                **(event.provenance_json or {}),
                "source": "deepseek_web_search_cleaning_bridge",
                "information_record_id": record.id,
            },
        )
        session.add(row)
        session.flush()
        record.epidemic_event_id = row.id
        record.cleaning_status = "event_created"
        created += 1
    session.flush()
    return {"epidemic_events_created": created, "event_duplicates": duplicate, "skipped": skipped}


def link_cleaning_outputs(session: Session, *, run_id: str) -> dict[str, int]:
    _unused = session
    _unused_run_id = run_id
    return {"linked": 0}


def assess_feature_readiness(session: Session, *, run_id: str) -> dict[str, int]:
    records = session.scalars(
        select(DiseaseCountryInformationRecord).where(DiseaseCountryInformationRecord.run_id == run_id)
    ).all()
    ready = sum(1 for record in records if record.feature_ready_potential and record.epidemic_event_id)
    evidence_only = sum(1 for record in records if record.cleaning_status == "evidence_only")
    return {"feature_ready_records": ready, "evidence_only_records": evidence_only}


def send_run_to_cleaning(session: Session, *, run_id: str) -> dict[str, int]:
    raw = persist_records_as_raw_documents(session, run_id=run_id)
    events = run_cleaner_for_records(session, run_id=run_id)
    readiness = assess_feature_readiness(session, run_id=run_id)
    return {**raw, **events, **readiness}


def _eligible_for_cleaning(record: DiseaseCountryInformationRecord) -> bool:
    return bool(
        record.metric_value is not None
        and record.metric_name
        and (record.event_date is not None or record.iso_week)
        and record.disease
        and record.country
    )


def _get_or_create_source(session: Session, record: DiseaseCountryInformationRecord) -> SourceRegistry:
    existing = session.scalar(select(SourceRegistry).where(SourceRegistry.base_url == record.source_url))
    if existing is not None:
        return existing
    source = SourceRegistry(
        name=record.source_title or record.source_domain or "DeepSeek Web Search Source",
        base_url=record.source_url,
        country_scope=[record.country],
        disease_scope=[record.disease],
        source_type=record.source_type or "web_evidence",
        authority_score=0.45,
        relevance_score=record.confidence,
        freshness_score=0.4,
        reliability_score=0.45,
        structured_data_score=0.75 if record.feature_ready_potential else 0.25,
        historical_success_score=0.0,
        language_country_match=0.6,
        legal_or_robots_penalty=0.0,
        robots_allowed=None,
        parser_type="deepseek_safe_summary",
        status="pending_review",
        metadata_json={
            "source": "deepseek_web_search_agent",
            "run_id": record.run_id,
            "requires_human_review": True,
        },
    )
    session.add(source)
    session.flush()
    return source


def _structured_event(record: DiseaseCountryInformationRecord) -> dict[str, Any]:
    metric_payload: dict[str, Any] = {}
    if record.metric_name and record.metric_value is not None:
        if record.metric_name == "outbreaks_count":
            metric_payload["outbreaks_count"] = int(record.metric_value)
        elif record.metric_name == "cases":
            metric_payload["cases"] = int(record.metric_value)
        else:
            metric_payload[record.metric_name] = record.metric_value
    return {
        "country": record.country,
        "disease": record.disease,
        "event_date": record.event_date.isoformat() if record.event_date else None,
        "iso_week": record.iso_week,
        "source_type": record.source_type,
        "source_key": "deepseek_web_search",
        **metric_payload,
    }
