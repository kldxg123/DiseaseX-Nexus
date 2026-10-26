from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from common.db import get_session, init_db
from common.models import EpidemicEvent, RawDocument, SourceRegistry, utc_now
from deepseek_web_search_agent.query_request import build_disease_time_query_request
from deepseek_web_search_agent.source_pack_builder import load_source_pack
from deepseek_web_search_agent.staging_review import load_review_table
from deepseek_web_search_agent.table_validator import row_has_blocking_issues, validate_extracted_table_rows


def ingest_approved_table_rows(
    *,
    review_table_path: str,
    database_url: str | None = None,
    allow_persistence: bool = False,
    output_dir: str | None = None,
) -> dict[str, Any]:
    if not allow_persistence:
        preview = preview_approved_table_ingestion(review_table_path=review_table_path)
        return {
            "status": "blocked",
            "approved_rows_seen": preview.get("approved_rows_seen", 0),
            "approved_rows_ingested": 0,
            "epidemic_events_created": 0,
            "feature_generation_allowed": False,
            "feature_eligibility_blockers": preview.get("feature_eligibility_blockers", []),
            "warnings": ["allow_persistence_required"],
        }
    review_path = Path(review_table_path)
    run_dir = review_path.parent
    db_url = database_url or f"sqlite:///{(run_dir / 'deepseek_table_review.sqlite').as_posix()}"
    init_db(db_url).dispose()
    rows = load_review_table(review_path)
    approved = [row for row in rows if str(row.get("approval_status") or "").lower() == "approved"]
    request = _load_query_request(run_dir)
    sources = load_source_pack(run_dir / "source_map.json")
    validated, issues = validate_extracted_table_rows(approved, request, sources)
    blocked = {row.get("row_id") for row in validated if row_has_blocking_issues(str(row.get("row_id")), issues)}
    created = 0
    with get_session(db_url) as session:
        for row in validated:
            if row.get("row_id") in blocked:
                continue
            source = _ensure_source(session, row, sources)
            raw_document = _create_raw_document(session, row, source)
            existing_event = session.scalar(
                select(EpidemicEvent).where(EpidemicEvent.raw_document_id == raw_document.id)
            )
            if existing_event is not None:
                continue
            event = EpidemicEvent(
                raw_document_id=raw_document.id,
                country=row.get("country"),
                admin1=row.get("region"),
                disease=row.get("disease"),
                event_date=date.fromisoformat(str(row.get("date"))),
                iso_week=_iso_week(str(row.get("date"))),
                cases=row.get("confirmed_cases"),
                confirmed_cases=row.get("confirmed_cases"),
                suspected_cases=row.get("suspected_cases"),
                deaths=row.get("confirmed_deaths"),
                confidence_score=float(row.get("extraction_confidence") or 0.0),
                extraction_method="deepseek_table_review_human_approved",
                provenance_json={
                    "source_id": row.get("source_id"),
                    "review_row_id": row.get("row_id"),
                    "approval_status": row.get("approval_status"),
                    "contacts_under_monitoring": row.get("contacts_under_monitoring"),
                    "evidence_summary": str(row.get("evidence_summary") or "")[:240],
                    "requires_human_review": True,
                },
            )
            session.add(event)
            session.flush()
            created += 1
    out_dir = Path(output_dir or run_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    feature_allowed, feature_blockers = _feature_eligibility(validated)
    summary = {
        "status": "ok",
        "review_table": str(review_path),
        "database_url_provided": bool(database_url),
        "approved_rows_seen": len(approved),
        "approved_rows_ingested": created,
        "epidemic_events_created": created,
        "blocked_approved_rows": len(blocked),
        "feature_generation_allowed": feature_allowed,
        "feature_eligibility_blockers": feature_blockers,
        "validation_issues_count": len(issues),
        "network_accessed": False,
        "deepseek_called_in_this_step": False,
    }
    paths = _write_ingestion_reports(out_dir, summary)
    return {**summary, "paths": paths}


def preview_approved_table_ingestion(*, review_table_path: str) -> dict[str, Any]:
    review_path = Path(review_table_path)
    run_dir = review_path.parent
    rows = load_review_table(review_path)
    approved = [row for row in rows if str(row.get("approval_status") or "").lower() == "approved"]
    request = _load_query_request(run_dir)
    sources = load_source_pack(run_dir / "source_map.json")
    validated, issues = validate_extracted_table_rows(approved, request, sources)
    blocked = {row.get("row_id") for row in validated if row_has_blocking_issues(str(row.get("row_id")), issues)}
    eligible_rows = [row for row in validated if row.get("row_id") not in blocked]
    feature_allowed, feature_blockers = _feature_eligibility(eligible_rows)
    return {
        "status": "ok",
        "review_table": str(review_path),
        "approved_rows_seen": len(approved),
        "approved_rows_passed_validator": len(eligible_rows),
        "blocked_approved_rows": len(blocked),
        "epidemic_events_would_create": len(eligible_rows),
        "feature_generation_allowed": feature_allowed,
        "feature_eligibility_blockers": feature_blockers,
        "validation_issues_count": len(issues),
        "network_accessed": False,
        "deepseek_called_in_this_step": False,
        "limitations": "Preview only; no database write is performed.",
    }


def _load_query_request(run_dir: Path):
    payload = _read_json(run_dir / "query_request.json")
    return build_disease_time_query_request(
        disease=str(payload.get("disease") or "unknown"),
        country_or_region=str(payload.get("country_or_region") or "unknown"),
        date_start=str(payload.get("date_start") or "1900-01-01"),
        date_end=str(payload.get("date_end") or "2999-12-31"),
    )


def _ensure_source(session, row: dict[str, Any], sources) -> SourceRegistry:
    evidence = next((source for source in sources if source.source_id == row.get("source_id")), None)
    base_url = evidence.url_safe_summary if evidence else f"https://table-review.local/{row.get('source_id') or 'unknown'}"
    source = session.scalar(select(SourceRegistry).where(SourceRegistry.base_url == base_url))
    if source:
        return source
    source = SourceRegistry(
        name=(evidence.title_summary if evidence else f"Table Review {row.get('source_id') or 'Source'}")[:200],
        base_url=base_url,
        country_scope=[str(row.get("country") or "")],
        disease_scope=[str(row.get("disease") or "")],
        source_type=evidence.source_type if evidence else "deepseek_table_review",
        authority_score=0.5,
        relevance_score=0.7,
        freshness_score=0.5,
        reliability_score=0.5,
        structured_data_score=0.6,
        historical_success_score=0.0,
        language_country_match=0.5,
        legal_or_robots_penalty=0.0,
        robots_allowed=None,
        parser_type="deepseek_table_review",
        status="pending_review",
        metadata_json={"source_id": row.get("source_id"), "requires_human_review": True},
    )
    session.add(source)
    session.flush()
    return source


def _create_raw_document(session, row: dict[str, Any], source: SourceRegistry) -> RawDocument:
    row_id = str(row.get("row_id") or hashlib.sha256(json.dumps(row, sort_keys=True).encode("utf-8")).hexdigest()[:12])
    normalized_url = f"{source.base_url}#deepseek-table-review-{row_id}"
    content = json.dumps(
        {
            "row_id": row_id,
            "date": row.get("date"),
            "country": row.get("country"),
            "region": row.get("region"),
            "source_id": row.get("source_id"),
            "evidence_summary": str(row.get("evidence_summary") or "")[:240],
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    existing = session.scalar(select(RawDocument).where(RawDocument.content_hash == content_hash))
    if existing is not None:
        return existing
    document = RawDocument(
        source_registry_id=source.id,
        original_url=normalized_url,
        normalized_url=normalized_url,
        title=f"Approved table review row {row_id}",
        body_text=content,
        content_hash=content_hash,
        status_code=None,
        content_type="application/json",
        country_detected=row.get("country"),
        disease_detected=row.get("disease"),
        extraction_status="approved_table_review",
        fetched_at=utc_now(),
        metadata_json={"review_row_id": row_id, "approval_status": row.get("approval_status")},
    )
    session.add(document)
    session.flush()
    return document


def _feature_eligibility(rows: list[dict[str, Any]]) -> tuple[bool, list[str]]:
    weeks = {_iso_week(str(row.get("date"))) for row in rows if row.get("date")}
    blockers: list[str] = []
    if len(weeks) < 4:
        blockers.append("insufficient_distinct_iso_weeks")
    if len(rows) < 8:
        blockers.append("insufficient_approved_rows")
    return not blockers, blockers


def _write_ingestion_reports(output_dir: Path, summary: dict[str, Any]) -> dict[str, str]:
    paths = {
        "approval_ingestion_summary": str(output_dir / "approval_ingestion_summary.json"),
        "promoted_epidemic_events_summary": str(output_dir / "promoted_epidemic_events_summary.json"),
        "feature_eligibility_after_table_ingestion": str(output_dir / "feature_eligibility_after_table_ingestion.json"),
        "rollback_commands": str(output_dir / "rollback_commands.md"),
    }
    for key, path in paths.items():
        if key == "rollback_commands":
            Path(path).write_text("# Rollback Commands\n\n- Delete pilot DB rows manually if needed; no remote action was performed.\n", encoding="utf-8")
        else:
            Path(path).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return paths


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _iso_week(value: str) -> str:
    parsed = date.fromisoformat(value[:10])
    year, week, _ = parsed.isocalendar()
    return f"{year}-W{week:02d}"
