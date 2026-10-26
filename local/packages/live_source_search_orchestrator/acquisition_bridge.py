from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from sqlalchemy.orm import Session

from cleaner_agent.agent import CleanerAgent
from common.models import EpidemicEvent
from crawler_agent.connectors import ConnectorDocument
from crawler_agent.connectors.base import document_from_html
from crawler_agent.content_extraction import ExtractedContent, extract_content
from crawler_agent.generic_parsers import parse_by_strategy, parser_result_to_connector_documents
from crawler_agent.http_fetcher import FetchedPage
from crawler_agent.raw_document_store import RawDocumentStore
from crawler_agent.source_registry import SourceRegistryService

READ_LIMIT_BYTES = 65536
FILE_LIKE_CONTENT_TYPES = (
    "application/pdf",
    "application/zip",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument",
)


@dataclass(frozen=True)
class CandidateApprovalDecision:
    approved: bool = True
    reviewer: str = "local_manual_review"
    decision: str = "approved_for_controlled_acquisition"
    notes_summary: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CandidateAcquisitionPreflight:
    allowed: bool
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    request_count_limit: int = 1
    follow_links: bool = False
    files_downloaded: bool = False
    full_body_saved: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CandidateAcquisitionPlan:
    run_id: str
    source_run_dir: str
    output_dir: str
    candidate: dict[str, Any] | None
    metadata_result: dict[str, Any] | None
    strategy: str
    max_requests: int = 1
    country: str | None = None
    disease: str | None = None
    approval: CandidateApprovalDecision = field(default_factory=CandidateApprovalDecision)
    preflight: CandidateAcquisitionPreflight = field(default_factory=lambda: CandidateAcquisitionPreflight(True))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CandidateAcquisitionResult:
    status: str
    run_id: str
    output_dir: str
    candidate_title: str | None
    candidate_domain: str | None
    candidate_url_hash_or_safe_url_summary: str | None
    request_count: int
    network_accessed: bool
    follow_links: bool
    files_downloaded: bool
    full_body_saved: bool
    content_type: str | None
    parser_strategy_used: str
    connector_documents_created: int
    raw_documents_saved: int
    epidemic_events_created: int
    model_features_created: int
    event_extraction_status: str
    source_registry_status: str
    recommended_next_step: str
    warnings: list[str] = field(default_factory=list)
    acquisition_plan_path: str | None = None
    acquisition_summary_path: str | None = None
    safety_audit_path: str | None = None
    next_connector_recommendation_path: str | None = None
    connector_document_summary_path: str | None = None
    raw_document_ingest_summary_path: str | None = None
    event_extraction_summary_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CandidateAcquisitionReport:
    plan: CandidateAcquisitionPlan
    result: CandidateAcquisitionResult

    def to_dict(self) -> dict[str, Any]:
        return {"plan": self.plan.to_dict(), "result": self.result.to_dict()}


def load_refined_review_queue(run_dir: str | Path) -> dict[str, Any]:
    root = Path(run_dir)
    review_path = root / "refined_review_queue.json"
    metadata_path = root / "metadata_check_results.json"
    quality_path = root / "candidate_quality_gate.json"
    summary_path = root / "live_source_search_summary.json"
    return {
        "run_dir": str(root),
        "review_queue": _read_json(review_path).get("review_queue", []),
        "metadata_results": _read_json(metadata_path).get("results", []),
        "quality_gate": _read_json(quality_path).get("candidate_quality_gate", []),
        "summary": _read_json(summary_path),
    }


def select_acquisition_candidate(
    review_queue: list[dict[str, Any]],
    metadata_results: list[dict[str, Any]],
    strategy: str = "top_confirmed",
) -> dict[str, Any] | None:
    if strategy != "top_confirmed":
        raise ValueError("strategy must be top_confirmed in this MVP.")
    metadata_by_url = {str(item.get("url") or ""): item for item in metadata_results if isinstance(item, dict)}
    scored: list[tuple[float, dict[str, Any]]] = []
    for item in review_queue:
        if not isinstance(item, dict):
            continue
        if _is_rejected_or_low_quality(item):
            continue
        url = _candidate_url(item)
        metadata = metadata_by_url.get(url) if url else None
        if not metadata:
            metadata = _find_metadata_for_candidate(item, metadata_results)
            url = str((metadata or {}).get("url") or url)
        if not metadata:
            continue
        likelihood = _safe_float(metadata.get("data_source_likelihood"))
        status = str(metadata.get("status") or "")
        if status not in {"data_source_candidate", "confirmed_data_source", "high_data_likelihood"} and likelihood < 0.62:
            continue
        domain = str(item.get("domain") or metadata.get("domain") or "")
        score = (
            _domain_priority(domain)
            + likelihood
            + _safe_float(item.get("refined_score"))
            + _safe_float(item.get("source_score")) * 0.25
        )
        scored.append((score, {**item, "url": url, "domain": domain, "metadata_result": metadata}))
    if not scored:
        return None
    return sorted(scored, key=lambda row: row[0], reverse=True)[0][1]


def build_candidate_acquisition_plan(
    candidate: dict[str, Any] | None,
    output_dir: str | Path,
    *,
    run_dir: str | Path | None = None,
    metadata_result: dict[str, Any] | None = None,
    strategy: str = "top_confirmed",
    max_requests: int = 1,
    country: str | None = None,
    disease: str | None = None,
) -> CandidateAcquisitionPlan:
    if int(max_requests) > 1:
        preflight = CandidateAcquisitionPreflight(False, reasons=["max_requests_must_be_lte_1"])
    elif candidate is None:
        preflight = CandidateAcquisitionPreflight(False, reasons=["no_candidate_selected"])
    else:
        preflight = _preflight_candidate(candidate, metadata_result=metadata_result, max_requests=int(max_requests))
    root = _make_acquisition_run_dir(output_dir, country=country, disease=disease)
    plan = CandidateAcquisitionPlan(
        run_id=root.name,
        source_run_dir=str(run_dir or ""),
        output_dir=str(root),
        candidate=_safe_candidate(candidate) if candidate else None,
        metadata_result=_safe_metadata(metadata_result or (candidate or {}).get("metadata_result")),
        strategy=strategy,
        max_requests=int(max_requests),
        country=country,
        disease=disease,
        preflight=preflight,
    )
    _write_plan_files(root, plan)
    return plan


def run_candidate_acquisition(
    plan: CandidateAcquisitionPlan,
    *,
    allow_network: bool = False,
    terms_reviewed: bool = False,
    manual_reviewed: bool = False,
    transport=None,
    session: Session | None = None,
    timeout_seconds: float = 15.0,
) -> CandidateAcquisitionResult:
    root = Path(plan.output_dir)
    root.mkdir(parents=True, exist_ok=True)
    if not plan.preflight.allowed:
        result = _result_from_failure(plan, status="no_candidate_selected", warnings=plan.preflight.reasons)
        return _write_result_files(root, plan, result, connector_documents=[], cleaner_summary={})
    if not (allow_network and terms_reviewed and manual_reviewed):
        result = _result_from_failure(plan, status="denied", warnings=["allow_network, terms_reviewed, and manual_reviewed are required."])
        return _write_result_files(root, plan, result, connector_documents=[], cleaner_summary={})
    if int(plan.max_requests) > 1:
        result = _result_from_failure(plan, status="denied", warnings=["max_requests must be <= 1."])
        return _write_result_files(root, plan, result, connector_documents=[], cleaner_summary={})
    candidate = plan.candidate or {}
    url = _candidate_url(candidate)
    if not url:
        result = _result_from_failure(plan, status="failed", warnings=["candidate URL is missing."])
        return _write_result_files(root, plan, result, connector_documents=[], cleaner_summary={})
    try:
        acquisition = _fetch_candidate_url(url, transport=transport, timeout_seconds=timeout_seconds)
    except Exception as exc:
        result = _result_from_failure(plan, status="failed", warnings=[f"Acquisition failed safely: {type(exc).__name__}."])
        return _write_result_files(root, plan, result, connector_documents=[], cleaner_summary={})
    if acquisition["status"] != "ok":
        result = _result_from_acquisition(plan, acquisition, connector_documents=[], raw_saved=0, events_created=0)
        _write_result_files(root, plan, result, connector_documents=[], cleaner_summary={})
        return result
    connector_documents = _connector_documents_from_acquisition(plan, acquisition)
    raw_saved = 0
    cleaner_summary: dict[str, Any] = {}
    if session is not None and connector_documents:
        raw_saved = _store_documents(session, plan, acquisition, connector_documents)
        cleaner_summary = CleanerAgent(session).process_pending(
            limit=max(1, len(connector_documents) + 2),
            country=plan.country,
            disease=plan.disease,
        )
        session.flush()
    events_created = int(cleaner_summary.get("events_created") or 0)
    result = _result_from_acquisition(
        plan,
        acquisition,
        connector_documents=connector_documents,
        raw_saved=raw_saved,
        events_created=events_created,
    )
    return _write_result_files(root, plan, result, connector_documents=connector_documents, cleaner_summary=cleaner_summary)


def candidate_acquisition_result_to_connector_document(result: CandidateAcquisitionResult) -> ConnectorDocument | None:
    if result.connector_documents_created <= 0:
        return None
    return ConnectorDocument(
        connector_name="review_to_acquisition_bridge",
        source_name=result.candidate_title or "candidate source",
        url=result.candidate_url_hash_or_safe_url_summary or "https://example.invalid/",
        title=result.candidate_title,
        body_text="ConnectorDocument summary placeholder; see raw_documents in pilot DB.",
        content_type=result.content_type or "text/plain",
        metadata={"parser_strategy": result.parser_strategy_used, "status": result.status},
    )


def list_review_candidates(run_dir: str | Path) -> dict[str, Any]:
    payload = load_refined_review_queue(run_dir)
    candidates = payload["review_queue"]
    metadata = payload["metadata_results"]
    selected = select_acquisition_candidate(candidates, metadata)
    return {
        "status": "ok",
        "run_dir": str(run_dir),
        "candidate_count": len(candidates),
        "metadata_result_count": len(metadata),
        "selected_candidate": _safe_candidate(selected) if selected else None,
        "candidates": [_safe_candidate(_with_metadata_url(item, metadata)) for item in candidates[:25]],
    }


def create_plan_from_run_dir(
    run_dir: str | Path,
    *,
    output_dir: str | Path,
    strategy: str = "top_confirmed",
    max_requests: int = 1,
) -> CandidateAcquisitionPlan:
    payload = load_refined_review_queue(run_dir)
    summary = payload.get("summary") or {}
    selected = select_acquisition_candidate(payload["review_queue"], payload["metadata_results"], strategy=strategy)
    metadata = (selected or {}).get("metadata_result") if selected else None
    return build_candidate_acquisition_plan(
        selected,
        output_dir,
        run_dir=run_dir,
        metadata_result=metadata,
        strategy=strategy,
        max_requests=max_requests,
        country=summary.get("country"),
        disease=summary.get("disease"),
    )


def _fetch_candidate_url(url: str, *, transport=None, timeout_seconds: float) -> dict[str, Any]:
    with httpx.Client(
        follow_redirects=False,
        timeout=float(timeout_seconds),
        transport=transport,
        headers={
            "User-Agent": "infectious-intel-local-candidate-acquisition/0.1",
            "Range": f"bytes=0-{READ_LIMIT_BYTES - 1}",
        },
    ) as client:
        with client.stream("GET", url) as response:
            content_type = (response.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
            status_code = int(response.status_code)
            if 300 <= status_code < 400:
                return _acquisition_payload(url, status="redirect_not_followed", status_code=status_code, content_type=content_type, warnings=["Redirect was not followed."])
            if status_code >= 400:
                return _acquisition_payload(url, status="failed", status_code=status_code, content_type=content_type, warnings=[f"HTTP {status_code}."])
            if _is_file_like(content_type, url):
                return _acquisition_payload(url, status="file_download_candidate", status_code=status_code, content_type=content_type, warnings=["File-like content was not downloaded."])
            text = _read_limited_text(response)
            return _acquisition_payload(url, status="ok", status_code=status_code, content_type=content_type, text=text)


def _connector_documents_from_acquisition(plan: CandidateAcquisitionPlan, acquisition: dict[str, Any]) -> list[ConnectorDocument]:
    candidate = plan.candidate or {}
    text = str(acquisition.get("text") or "")
    parser_strategy = _parser_strategy(plan, acquisition)
    source_name = str(candidate.get("title") or candidate.get("source_name") or candidate.get("domain") or "candidate source")
    url = str(candidate.get("url") or "")
    metadata = {
        "connector": "review_to_acquisition_bridge",
        "parser_strategy": parser_strategy,
        "source_type": candidate.get("source_type"),
        "quality_gate_status": candidate.get("quality_gate_status"),
        "metadata_check_status": candidate.get("metadata_check_status"),
        "data_source_likelihood": candidate.get("data_source_likelihood"),
        "country_detected": plan.country,
        "disease_detected": plan.disease,
        "content_hash_prefix": _hash(text)[:12],
    }
    if not text:
        return []
    if parser_strategy in {"html_table", "json_api", "csv_dataset", "official_report_page"}:
        try:
            parser_result = parse_by_strategy(
                parser_strategy,
                text,
                source_name=source_name,
                url=url,
                country=plan.country,
                disease=plan.disease,
            )
            documents = parser_result_to_connector_documents(parser_result)
            if documents:
                return documents[:25]
        except Exception:
            pass
    if (acquisition.get("content_type") or "").startswith("text/html"):
        document = document_from_html(
            connector_name="review_to_acquisition_bridge",
            source_name=source_name,
            url=url,
            html=text,
            metadata=metadata,
        )
        return [_clip_document(document)]
    extracted = extract_content(text)
    return [
        ConnectorDocument(
            connector_name="review_to_acquisition_bridge",
            source_name=source_name,
            url=url,
            title=extracted.title or source_name,
            body_text=_clip_text(extracted.body_text or text, 12000),
            content_type=str(acquisition.get("content_type") or "text/plain"),
            metadata=metadata,
        )
    ]


def _store_documents(
    session: Session,
    plan: CandidateAcquisitionPlan,
    acquisition: dict[str, Any],
    documents: list[ConnectorDocument],
) -> int:
    candidate = plan.candidate or {}
    source = SourceRegistryService(session).upsert_source(
        name=str(candidate.get("title") or candidate.get("source_name") or candidate.get("domain") or "candidate source"),
        base_url=str(candidate.get("url") or ""),
        country_scope=[plan.country] if plan.country else [],
        disease_scope=[plan.disease] if plan.disease else [],
        source_type=str(candidate.get("source_type") or "unknown"),
        authority_score=0.8 if str(candidate.get("domain") or "").endswith(".gov") else 0.5,
        relevance_score=_safe_float(candidate.get("data_source_likelihood")) or _safe_float(candidate.get("source_score")),
        freshness_score=0.5,
        reliability_score=0.7,
        structured_data_score=_safe_float(candidate.get("data_source_likelihood")),
        historical_success_score=0.1,
        language_country_match=0.7,
        legal_or_robots_penalty=0.0,
        robots_allowed=None,
        parser_type=str(candidate.get("parser_strategy") or _parser_strategy(plan, acquisition)),
        status="pending_review",
        metadata_json={
            "bridge": "review_to_acquisition",
            "quality_gate_status": candidate.get("quality_gate_status"),
            "metadata_check_status": candidate.get("metadata_check_status"),
            "data_source_likelihood": candidate.get("data_source_likelihood"),
            "source_registry_status": "pending_review",
        },
    )
    store = RawDocumentStore(session)
    created = 0
    for document in documents:
        fetched = FetchedPage(
            url=document.url,
            final_url=document.url,
            status_code=int(acquisition.get("status_code") or 200),
            content_type=document.content_type,
            text=document.body_text,
        )
        result = store.save(
            source=source,
            fetched_page=fetched,
            extracted_content=ExtractedContent(title=document.title, body_text=document.body_text),
        )
        result.document.country_detected = plan.country
        result.document.disease_detected = str(plan.disease).lower() if plan.disease else None
        result.document.metadata_json = {
            **(result.document.metadata_json or {}),
            "bridge": "review_to_acquisition",
            "connector": document.connector_name,
            "parser_strategy": document.metadata.get("parser_strategy"),
            "source_type": document.metadata.get("source_type"),
            "content_hash_prefix": document.metadata.get("content_hash_prefix"),
        }
        created += 1 if result.created else 0
    return created


def _result_from_acquisition(
    plan: CandidateAcquisitionPlan,
    acquisition: dict[str, Any],
    *,
    connector_documents: list[ConnectorDocument],
    raw_saved: int,
    events_created: int,
) -> CandidateAcquisitionResult:
    candidate = plan.candidate or {}
    parser_strategy = _parser_strategy(plan, acquisition)
    event_status = "extracted" if events_created > 0 else "insufficient_structured_signal"
    if acquisition.get("status") in {"failed", "redirect_not_followed", "file_download_candidate"}:
        event_status = str(acquisition.get("status"))
    return CandidateAcquisitionResult(
        status="ok" if acquisition.get("status") == "ok" else str(acquisition.get("status") or "failed"),
        run_id=plan.run_id,
        output_dir=plan.output_dir,
        candidate_title=candidate.get("title"),
        candidate_domain=candidate.get("domain"),
        candidate_url_hash_or_safe_url_summary=_safe_url_summary(str(candidate.get("url") or "")),
        request_count=1,
        network_accessed=True,
        follow_links=False,
        files_downloaded=False,
        full_body_saved=False,
        content_type=acquisition.get("content_type"),
        parser_strategy_used=parser_strategy,
        connector_documents_created=len(connector_documents),
        raw_documents_saved=int(raw_saved),
        epidemic_events_created=int(events_created),
        model_features_created=0,
        event_extraction_status=event_status,
        source_registry_status="pending_review",
        recommended_next_step=_next_step(parser_strategy, events_created, acquisition),
        warnings=list(acquisition.get("warnings") or []),
    )


def _result_from_failure(plan: CandidateAcquisitionPlan, *, status: str, warnings: list[str]) -> CandidateAcquisitionResult:
    candidate = plan.candidate or {}
    return CandidateAcquisitionResult(
        status=status,
        run_id=plan.run_id,
        output_dir=plan.output_dir,
        candidate_title=candidate.get("title"),
        candidate_domain=candidate.get("domain"),
        candidate_url_hash_or_safe_url_summary=_safe_url_summary(str(candidate.get("url") or "")) if candidate else None,
        request_count=0,
        network_accessed=False,
        follow_links=False,
        files_downloaded=False,
        full_body_saved=False,
        content_type=None,
        parser_strategy_used=str(candidate.get("parser_strategy") or "metadata_only"),
        connector_documents_created=0,
        raw_documents_saved=0,
        epidemic_events_created=0,
        model_features_created=0,
        event_extraction_status=status,
        source_registry_status="pending_review",
        recommended_next_step="Resolve preflight warnings before acquisition.",
        warnings=warnings,
    )


def _write_plan_files(root: Path, plan: CandidateAcquisitionPlan) -> None:
    _write_json(root / "candidate_acquisition_plan.json", plan.to_dict())
    (root / "candidate_acquisition_plan.md").write_text(_plan_markdown(plan), encoding="utf-8")


def _write_result_files(
    root: Path,
    plan: CandidateAcquisitionPlan,
    result: CandidateAcquisitionResult,
    *,
    connector_documents: list[ConnectorDocument],
    cleaner_summary: dict[str, Any],
) -> CandidateAcquisitionResult:
    result = _attach_paths(root, result)
    _write_plan_files(root, plan)
    _write_json(root / "candidate_acquisition_summary.json", result.to_dict())
    (root / "candidate_acquisition_summary.md").write_text(_summary_markdown(result), encoding="utf-8")
    (root / "candidate_acquisition_safety_audit.md").write_text(_safety_markdown(result), encoding="utf-8")
    _write_json(
        root / "connector_document_summary.json",
        {
            "connector_documents_created": len(connector_documents),
            "documents": [
                {
                    "connector_name": doc.connector_name,
                    "source_name": doc.source_name,
                    "title": doc.title,
                    "content_type": doc.content_type,
                    "body_text_length": len(doc.body_text or ""),
                }
                for doc in connector_documents[:25]
            ],
        },
    )
    _write_json(
        root / "raw_document_ingest_summary.json",
        {"raw_documents_saved": result.raw_documents_saved, "source_registry_status": "pending_review"},
    )
    _write_json(
        root / "event_extraction_summary.json",
        {
            "event_extraction_status": result.event_extraction_status,
            "epidemic_events_created": result.epidemic_events_created,
            "cleaner_summary": cleaner_summary,
        },
    )
    _write_json(root / "feature_generation_summary.json", {"model_features_created": result.model_features_created})
    _write_json(root / "next_connector_recommendation.json", _next_connector_recommendation(result))
    (root / "rollback_commands.md").write_text(_rollback_markdown(), encoding="utf-8")
    return result


def _attach_paths(root: Path, result: CandidateAcquisitionResult) -> CandidateAcquisitionResult:
    from dataclasses import replace

    return replace(
        result,
        acquisition_plan_path=str(root / "candidate_acquisition_plan.json"),
        acquisition_summary_path=str(root / "candidate_acquisition_summary.json"),
        safety_audit_path=str(root / "candidate_acquisition_safety_audit.md"),
        next_connector_recommendation_path=str(root / "next_connector_recommendation.json"),
        connector_document_summary_path=str(root / "connector_document_summary.json"),
        raw_document_ingest_summary_path=str(root / "raw_document_ingest_summary.json"),
        event_extraction_summary_path=str(root / "event_extraction_summary.json"),
    )


def _preflight_candidate(candidate: dict[str, Any], *, metadata_result: dict[str, Any] | None, max_requests: int) -> CandidateAcquisitionPreflight:
    reasons: list[str] = []
    warnings: list[str] = []
    if int(max_requests) > 1:
        reasons.append("max_requests_must_be_lte_1")
    if _is_rejected_or_low_quality(candidate):
        reasons.append("candidate_rejected_or_low_quality")
    if not _candidate_url(candidate):
        reasons.append("candidate_url_missing")
    metadata = metadata_result or candidate.get("metadata_result") or {}
    likelihood = _safe_float(metadata.get("data_source_likelihood") or candidate.get("data_source_likelihood"))
    status = str(metadata.get("status") or candidate.get("metadata_check_status") or "")
    if not metadata:
        reasons.append("metadata_check_required_before_acquisition")
    elif status not in {"data_source_candidate", "confirmed_data_source", "high_data_likelihood"} and likelihood < 0.62:
        reasons.append("metadata_check_not_confirmed")
    if str(candidate.get("domain") or "").startswith("jobs."):
        reasons.append("jobs_domain_rejected")
    return CandidateAcquisitionPreflight(
        allowed=not reasons,
        reasons=sorted(set(reasons)),
        warnings=warnings,
        request_count_limit=1,
        follow_links=False,
        files_downloaded=False,
        full_body_saved=False,
    )


def _read_limited_text(response: httpx.Response) -> str:
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_bytes():
        if not chunk:
            continue
        remaining = READ_LIMIT_BYTES - total
        if remaining <= 0:
            break
        piece = chunk[:remaining]
        chunks.append(piece)
        total += len(piece)
        if total >= READ_LIMIT_BYTES:
            break
    return b"".join(chunks).decode(response.encoding or "utf-8", errors="ignore")


def _parser_strategy(plan: CandidateAcquisitionPlan, acquisition: dict[str, Any]) -> str:
    metadata = plan.metadata_result or (plan.candidate or {}).get("metadata_result") or {}
    hint = str(metadata.get("parser_strategy_hint") or "").strip()
    if hint and hint != "metadata_only":
        return _normalize_parser_strategy(hint)
    candidate = plan.candidate or {}
    return _normalize_parser_strategy(str(candidate.get("parser_strategy") or candidate.get("source_type") or "official_report_page"))


def _normalize_parser_strategy(value: str) -> str:
    text = str(value or "").strip().lower()
    mapping = {
        "generic_html": "official_report_page",
        "download_metadata": "official_report_page",
        "dashboard_metadata": "dashboard_data_source",
        "metadata_only": "official_report_page",
        "unknown": "official_report_page",
    }
    return mapping.get(text, text or "official_report_page")


def _acquisition_payload(
    url: str,
    *,
    status: str,
    status_code: int,
    content_type: str,
    text: str | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "url": url,
        "domain": urlsplit(url).hostname or "",
        "status": status,
        "status_code": status_code,
        "content_type": content_type,
        "text": text or "",
        "text_hash_prefix": _hash(text or "")[:12],
        "warnings": warnings or [],
    }


def _is_file_like(content_type: str, url: str) -> bool:
    lowered = f"{content_type} {url}".lower()
    return any(content_type.startswith(item) for item in FILE_LIKE_CONTENT_TYPES) or any(
        lowered.endswith(ext) for ext in (".pdf", ".zip", ".xlsx", ".xls")
    )


def _is_rejected_or_low_quality(item: dict[str, Any]) -> bool:
    text = " ".join(str(item.get(key) or "") for key in ("domain", "title", "quality_gate_status", "source_type", "rejection_reason")).lower()
    if "jobs.cdc.gov" in text or "career" in text or "employment" in text:
        return True
    if str(item.get("quality_gate_status") or "").lower() == "rejected":
        return True
    if str(item.get("source_type") or "").lower() == "low_relevance" and _safe_float(item.get("data_source_likelihood")) < 0.62:
        return True
    return False


def _find_metadata_for_candidate(candidate: dict[str, Any], metadata_results: list[dict[str, Any]]) -> dict[str, Any] | None:
    domain = str(candidate.get("domain") or "").lower()
    title = str(candidate.get("title") or "").lower()
    status_hint = str(candidate.get("metadata_check_status") or "").lower()
    likelihood_hint = _safe_float(candidate.get("data_source_likelihood"))
    best: tuple[float, dict[str, Any]] | None = None
    for metadata in metadata_results:
        if not isinstance(metadata, dict):
            continue
        metadata_domain = str(metadata.get("domain") or urlsplit(str(metadata.get("url") or "")).hostname or "").lower()
        if domain and metadata_domain and metadata_domain != domain:
            continue
        metadata_title = str(metadata.get("title_summary") or "").lower()
        score = 0.0
        identity_score = 0.0
        if title and metadata_title:
            if title in metadata_title or metadata_title in title:
                identity_score += 1.0
            else:
                overlap_score = _token_overlap_score(title, metadata_title)
                if overlap_score >= 0.4:
                    identity_score += overlap_score
        if status_hint and status_hint == str(metadata.get("status") or "").lower():
            identity_score += 0.35
        metadata_likelihood = _safe_float(metadata.get("data_source_likelihood"))
        if likelihood_hint and abs(metadata_likelihood - likelihood_hint) <= 0.05:
            identity_score += 0.25
        if identity_score <= 0:
            continue
        score += identity_score
        score += metadata_likelihood * 0.2
        if score <= 0:
            continue
        if best is None or score > best[0]:
            best = (score, metadata)
    return best[1] if best else None


def _with_metadata_url(candidate: dict[str, Any], metadata_results: list[dict[str, Any]]) -> dict[str, Any]:
    if _candidate_url(candidate):
        return candidate
    metadata = _find_metadata_for_candidate(candidate, metadata_results)
    if not metadata:
        return candidate
    return {
        **candidate,
        "url": metadata.get("url"),
        "domain": candidate.get("domain") or metadata.get("domain"),
        "metadata_result": metadata,
    }


def _token_overlap_score(left: str, right: str) -> float:
    left_tokens = {token for token in _word_tokens(left) if len(token) >= 4}
    right_tokens = {token for token in _word_tokens(right) if len(token) >= 4}
    if not left_tokens or not right_tokens:
        return 0.0
    overlap = len(left_tokens & right_tokens)
    return overlap / max(1, len(left_tokens | right_tokens))


def _word_tokens(value: str) -> list[str]:
    cleaned = "".join(ch.lower() if ch.isalnum() else " " for ch in str(value))
    return [part for part in cleaned.split() if part]


def _candidate_url(item: dict[str, Any]) -> str:
    return str(item.get("url") or item.get("base_url") or item.get("normalized_url") or "").strip()


def _domain_priority(domain: str) -> float:
    domain = str(domain or "").lower()
    if domain in {"www.cdc.gov", "cdc.gov"}:
        return 1.0
    if domain.endswith(".cdc.gov") or domain in {"catalog.data.gov", "data.gov"}:
        return 0.85
    if domain.endswith(".gov"):
        return 0.65
    return 0.2


def _safe_candidate(candidate: dict[str, Any] | None) -> dict[str, Any] | None:
    if not candidate:
        return None
    url = _candidate_url(candidate)
    return {
        "candidate_id": candidate.get("candidate_id"),
        "title": candidate.get("title"),
        "domain": candidate.get("domain") or urlsplit(url).hostname,
        "url": url,
        "url_hash": _hash(url),
        "source_score": candidate.get("source_score"),
        "refined_score": candidate.get("refined_score"),
        "source_type": candidate.get("source_type"),
        "parser_strategy": candidate.get("parser_strategy"),
        "quality_gate_status": candidate.get("quality_gate_status"),
        "metadata_check_status": candidate.get("metadata_check_status"),
        "data_source_likelihood": candidate.get("data_source_likelihood"),
        "recommended_action": candidate.get("recommended_action"),
        "registry_status": "pending_review",
    }


def _safe_metadata(metadata: dict[str, Any] | None) -> dict[str, Any] | None:
    if not metadata:
        return None
    return {
        "url": metadata.get("url"),
        "domain": metadata.get("domain"),
        "status": metadata.get("status"),
        "http_status_code": metadata.get("http_status_code"),
        "content_type": metadata.get("content_type"),
        "data_source_likelihood": metadata.get("data_source_likelihood"),
        "detected_source_type": metadata.get("detected_source_type"),
        "parser_strategy_hint": metadata.get("parser_strategy_hint"),
        "recommended_action": metadata.get("recommended_action"),
    }


def _make_acquisition_run_dir(output_dir: str | Path, *, country: str | None, disease: str | None) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    slug = "_".join(_safe_slug(part) for part in (disease or "unknown", country or "unknown") if part)
    path = Path(output_dir) / f"acquisition_{slug}_{stamp}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_slug(value: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "_" for ch in str(value))
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return cleaned.strip("_") or "unknown"


def _clip_document(document: ConnectorDocument) -> ConnectorDocument:
    return ConnectorDocument(
        connector_name=document.connector_name,
        source_name=document.source_name,
        url=document.url,
        title=document.title,
        body_text=_clip_text(document.body_text, 12000),
        content_type=document.content_type,
        metadata=document.metadata,
    )


def _clip_text(value: str, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[:limit] + "...[truncated]"


def _safe_url_summary(url: str) -> str:
    parsed = urlsplit(str(url or ""))
    return f"{parsed.hostname or 'unknown'}:{_hash(url)[:12]}"


def _hash(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _next_step(parser_strategy: str, events_created: int, acquisition: dict[str, Any]) -> str:
    if acquisition.get("status") == "file_download_candidate":
        return "Manual approval is required before any file download or parser implementation."
    if events_created > 0:
        return "Review extracted events before feature generation or forecasting."
    if parser_strategy == "dashboard_data_source":
        return "Design a dedicated dashboard connector; do not scrape interactively without review."
    return "Raw document saved; event extraction was insufficient, so connector design should be reviewed manually."


def _next_connector_recommendation(result: CandidateAcquisitionResult) -> dict[str, Any]:
    return {
        "recommended_next_step": result.recommended_next_step,
        "parser_strategy_used": result.parser_strategy_used,
        "requires_human_review": True,
        "auto_enable": False,
        "source_registry_status": "pending_review",
        "limitations": "Connector recommendation only; no connector was activated.",
    }


def _plan_markdown(plan: CandidateAcquisitionPlan) -> str:
    candidate = plan.candidate or {}
    return "\n".join(
        [
            "# Candidate Acquisition Plan",
            "",
            f"- Status: {'allowed' if plan.preflight.allowed else 'blocked'}",
            f"- Candidate: {candidate.get('title')}",
            f"- Domain: {candidate.get('domain')}",
            f"- URL summary: {_safe_url_summary(str(candidate.get('url') or '')) if candidate else ''}",
            f"- Max requests: {plan.max_requests}",
            "- Follow links: false",
            "- Files downloaded: false",
            "- Source activation: false",
        ]
    ) + "\n"


def _summary_markdown(result: CandidateAcquisitionResult) -> str:
    return "\n".join(
        [
            "# Candidate Acquisition Summary",
            "",
            f"- Status: {result.status}",
            f"- Candidate: {result.candidate_title}",
            f"- Domain: {result.candidate_domain}",
            f"- Request count: {result.request_count}",
            f"- Content type: {result.content_type}",
            f"- Parser strategy: {result.parser_strategy_used}",
            f"- Connector documents created: {result.connector_documents_created}",
            f"- Raw documents saved: {result.raw_documents_saved}",
            f"- Epidemic events created: {result.epidemic_events_created}",
            f"- Event extraction status: {result.event_extraction_status}",
            f"- Source registry status: {result.source_registry_status}",
        ]
    ) + "\n"


def _safety_markdown(result: CandidateAcquisitionResult) -> str:
    return "\n".join(
        [
            "# Candidate Acquisition Safety Audit",
            "",
            f"- Request count: {result.request_count}",
            f"- Network accessed: {result.network_accessed}",
            f"- Follow links: {result.follow_links}",
            f"- Files downloaded: {result.files_downloaded}",
            f"- Full body saved: {result.full_body_saved}",
            "- Candidate page links were not visited.",
            "- Source remains pending_review.",
        ]
    ) + "\n"


def _rollback_markdown() -> str:
    return "\n".join(
        [
            "# Rollback Commands",
            "",
            "This run writes only to the selected local pilot database and report directory.",
            "Delete the pilot SQLite database after manual confirmation if rollback is needed.",
            "No remote rollback is required.",
        ]
    ) + "\n"


def _write_json(path: str | Path, payload: dict[str, Any]) -> None:
    Path(path).write_text(json.dumps(_sanitize(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _read_json(path: str | Path) -> dict[str, Any]:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _sanitize(value: Any) -> Any:
    forbidden = {"html", "response_body", "raw_text", "metadata_json", "feature_json", "provenance_json", "lesson_json"}
    sensitive = ("password", "token", "secret", "credential", "private", "ssh", "key")
    if isinstance(value, dict):
        safe: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if lowered in forbidden or any(part in lowered for part in sensitive):
                continue
            safe[str(key)] = _sanitize(item)
        return safe
    if isinstance(value, list):
        return [_sanitize(item) for item in value[:200]]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    text = str(value)
    lowered = text.lower()
    if any(part in lowered for part in sensitive):
        return "[redacted]"
    return text if len(text) <= 300 else text[:300] + "...[truncated]"


__all__ = [
    "CandidateAcquisitionPlan",
    "CandidateAcquisitionPreflight",
    "CandidateAcquisitionReport",
    "CandidateAcquisitionResult",
    "CandidateApprovalDecision",
    "build_candidate_acquisition_plan",
    "candidate_acquisition_result_to_connector_document",
    "create_plan_from_run_dir",
    "list_review_candidates",
    "load_refined_review_queue",
    "run_candidate_acquisition",
    "select_acquisition_candidate",
]
