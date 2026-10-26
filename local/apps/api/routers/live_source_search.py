from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from apps.api.deps import get_db_session
from apps.api.safety import sanitize_mapping
from apps.api.schemas import LiveSourceSearchRequestModel
from live_source_search_orchestrator import LiveSourceSearchRequest, run_live_source_search
from live_source_search_orchestrator.acquisition_bridge import (
    create_plan_from_run_dir,
    list_review_candidates,
    run_candidate_acquisition,
)
from live_source_search_orchestrator.event_quality_diagnostics import write_event_quality_diagnostic_report
from live_source_search_orchestrator.orchestrator import report_summary
from live_source_search_orchestrator.report import read_latest_report_dirs

router = APIRouter(prefix="/live-source-search", tags=["Live Source Search"])


@router.post("/dry-run")
def dry_run_live_source_search(request: LiveSourceSearchRequestModel) -> dict:
    report = run_live_source_search(_to_request(request, dry_run=True), session=None)
    return sanitize_mapping(report_summary(report), max_items=120)


@router.post("/run")
def run_controlled_live_source_search(
    request: LiveSourceSearchRequestModel,
    session: Session = Depends(get_db_session),
) -> dict:
    if not (request.allow_network and request.terms_reviewed and request.manual_reviewed):
        return sanitize_mapping(
            {
                "status": "failed",
                "country": request.country,
                "disease": request.disease,
                "request_count_total": 0,
                "candidate_urls_visited": False,
                "no_follow_links": True,
                "warnings": ["Controlled live source search requires network, terms, and manual review flags."],
            }
        )
    report = run_live_source_search(_to_request(request, dry_run=False), session=session)
    return sanitize_mapping(report_summary(report), max_items=120)


@router.post("/metadata-check")
def run_live_source_search_metadata_check(
    request: LiveSourceSearchRequestModel,
    session: Session = Depends(get_db_session),
) -> dict:
    if not (request.allow_network and request.terms_reviewed and request.manual_reviewed):
        return sanitize_mapping(
            {
                "status": "failed",
                "country": request.country,
                "disease": request.disease,
                "metadata_check_executed": False,
                "metadata_check_request_count": 0,
                "warnings": ["Metadata check requires network, terms, and manual review flags."],
                "no_follow_links": True,
                "files_downloaded": False,
                "full_body_saved": False,
            }
        )
    report = run_live_source_search(_to_request(request, dry_run=False, force_metadata_check=True), session=session)
    return sanitize_mapping(report_summary(report), max_items=120)


@router.get("/reports")
def list_live_source_search_reports(output_dir: str = "reports/live_source_search", limit: int = 20) -> dict:
    return sanitize_mapping({"reports": read_latest_report_dirs(output_dir, limit=max(1, min(int(limit), 100)))})


@router.get("/review-queue/{run_id}")
def get_live_source_search_review_queue(run_id: str, output_dir: str = "reports/live_source_search") -> dict:
    safe_run_id = "".join(ch for ch in str(run_id) if ch.isalnum() or ch in {"_", "-"})
    path = Path(output_dir) / safe_run_id / "review_queue.json"
    if not path.exists():
        return sanitize_mapping({"status": "not_found", "run_id": safe_run_id, "review_queue": []})
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {"review_queue": []}
    return sanitize_mapping({"status": "ok", "run_id": safe_run_id, "review_queue": payload.get("review_queue", [])})


@router.get("/review-queue/{run_id}/candidates")
def list_live_source_search_review_candidates(run_id: str, output_dir: str = "reports/live_source_search") -> dict:
    safe_run_id = "".join(ch for ch in str(run_id) if ch.isalnum() or ch in {"_", "-"})
    return sanitize_mapping(list_review_candidates(Path(output_dir) / safe_run_id), max_items=120)


@router.post("/acquisition/plan")
def plan_live_source_candidate_acquisition(request: dict) -> dict:
    run_dir = _run_dir_from_request(request)
    plan = create_plan_from_run_dir(
        run_dir,
        output_dir=str(request.get("output_dir") or "reports/live_source_search/acquisition_runs"),
        strategy=str(request.get("strategy") or "top_confirmed"),
        max_requests=int(request.get("max_requests") or 1),
    )
    candidate = plan.candidate or {}
    return sanitize_mapping(
        {
            "status": "ok" if plan.preflight.allowed else "blocked",
            "run_id": plan.run_id,
            "output_dir": plan.output_dir,
            "candidate_title": candidate.get("title"),
            "candidate_domain": candidate.get("domain"),
            "max_requests": plan.max_requests,
            "preflight": plan.preflight.to_dict(),
            "plan_path": str(Path(plan.output_dir) / "candidate_acquisition_plan.json"),
            "network_accessed": False,
            "follow_links": False,
            "files_downloaded": False,
            "full_body_saved": False,
        },
        max_items=120,
    )


@router.post("/acquisition/run")
def run_live_source_candidate_acquisition(
    request: dict,
    session: Session = Depends(get_db_session),
) -> dict:
    if not (request.get("allow_network") and request.get("terms_reviewed") and request.get("manual_reviewed")):
        return sanitize_mapping(
            {
                "status": "failed",
                "request_count": 0,
                "network_accessed": False,
                "follow_links": False,
                "files_downloaded": False,
                "full_body_saved": False,
                "warnings": ["Controlled acquisition requires network, terms, and manual review flags."],
            }
        )
    run_dir = _run_dir_from_request(request)
    plan = create_plan_from_run_dir(
        run_dir,
        output_dir=str(request.get("output_dir") or "reports/live_source_search/acquisition_runs"),
        strategy=str(request.get("strategy") or "top_confirmed"),
        max_requests=int(request.get("max_requests") or 1),
    )
    result = run_candidate_acquisition(
        plan,
        allow_network=True,
        terms_reviewed=True,
        manual_reviewed=True,
        session=session,
    )
    return sanitize_mapping(result.to_dict(), max_items=120)


@router.get("/acquisition/reports/{run_id}")
def get_live_source_candidate_acquisition_report(
    run_id: str,
    output_dir: str = "reports/live_source_search/acquisition_runs",
) -> dict:
    safe_run_id = "".join(ch for ch in str(run_id) if ch.isalnum() or ch in {"_", "-"})
    path = Path(output_dir) / safe_run_id / "candidate_acquisition_summary.json"
    if not path.exists():
        return sanitize_mapping({"status": "not_found", "run_id": safe_run_id})
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {"status": "failed"}
    return sanitize_mapping({"status": "ok", "run_id": safe_run_id, "summary": payload}, max_items=120)


@router.post("/acquisition/diagnose-quality")
def diagnose_live_source_candidate_acquisition_quality(request: dict) -> dict:
    run_dir_value = str(request.get("run_dir") or "").strip()
    run_dir = Path(run_dir_value) if run_dir_value else Path("reports/live_source_search/acquisition_runs") / str(request.get("run_id") or "")
    database_url = str(request.get("database_url") or "")
    if not database_url:
        return sanitize_mapping({"status": "failed", "warnings": ["database_url is required for local diagnostics."]})
    report = write_event_quality_diagnostic_report(run_dir, database_url=database_url)
    return sanitize_mapping(_quality_report_summary(report), max_items=120)


@router.get("/acquisition/quality-report/{run_id}")
def get_live_source_candidate_acquisition_quality_report(
    run_id: str,
    output_dir: str = "reports/live_source_search/acquisition_runs",
) -> dict:
    safe_run_id = "".join(ch for ch in str(run_id) if ch.isalnum() or ch in {"_", "-"})
    path = Path(output_dir) / safe_run_id / "event_quality_diagnostics.json"
    if not path.exists():
        return sanitize_mapping({"status": "not_found", "run_id": safe_run_id})
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {"status": "failed"}
    return sanitize_mapping({"status": "ok", "run_id": safe_run_id, "quality_report": _quality_payload_summary(payload)}, max_items=120)


@router.get("/metadata-check-results/{run_id}")
def get_live_source_search_metadata_results(run_id: str, output_dir: str = "reports/live_source_search") -> dict:
    safe_run_id = "".join(ch for ch in str(run_id) if ch.isalnum() or ch in {"_", "-"})
    path = Path(output_dir) / safe_run_id / "metadata_check_results.json"
    if not path.exists():
        return sanitize_mapping({"status": "not_found", "run_id": safe_run_id, "results": []})
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {"results": []}
    return sanitize_mapping({"status": "ok", "run_id": safe_run_id, "metadata_check_results": payload.get("results", [])})


def _run_dir_from_request(request: dict) -> Path:
    if request.get("run_dir"):
        return Path(str(request.get("run_dir")))
    output_dir = str(request.get("source_output_dir") or request.get("search_output_dir") or "reports/live_source_search")
    run_id = "".join(ch for ch in str(request.get("run_id") or "") if ch.isalnum() or ch in {"_", "-"})
    if not run_id:
        raise ValueError("run_dir or run_id is required.")
    return Path(output_dir) / run_id


def _quality_report_summary(report) -> dict:
    return {
        "status": report.status,
        "run_dir": report.run_dir,
        "raw_documents_count": report.raw_documents_count,
        "epidemic_events_count": report.epidemic_events_count,
        "model_features_count": report.model_features_count,
        "feature_ready_event_count": report.event_quality.feature_ready_event_count,
        "event_quality_status": report.event_quality.quality_status,
        "feature_eligible": report.feature_eligibility.eligible,
        "blocker_types": [item.blocker_type for item in report.blockers],
        "recommended_next_action": report.recommended_next_action,
        "report_paths": report.report_paths,
    }


def _quality_payload_summary(payload: dict) -> dict:
    event_quality = payload.get("event_quality") or {}
    feature_eligibility = payload.get("feature_eligibility") or {}
    return {
        "status": payload.get("status"),
        "raw_documents_count": payload.get("raw_documents_count"),
        "epidemic_events_count": payload.get("epidemic_events_count"),
        "model_features_count": payload.get("model_features_count"),
        "feature_ready_event_count": event_quality.get("feature_ready_event_count"),
        "event_quality_status": event_quality.get("quality_status"),
        "feature_eligible": feature_eligibility.get("eligible"),
        "blocker_types": [item.get("blocker_type") for item in payload.get("blockers") or [] if isinstance(item, dict)],
        "recommended_next_action": payload.get("recommended_next_action"),
        "report_paths": payload.get("report_paths") or {},
    }


def _to_request(
    request: LiveSourceSearchRequestModel,
    *,
    dry_run: bool,
    force_metadata_check: bool = False,
) -> LiveSourceSearchRequest:
    return LiveSourceSearchRequest(
        country=request.country,
        disease=request.preferred_live_disease or request.disease,
        preferred_live_disease=request.preferred_live_disease,
        providers=request.providers or request.provider,
        max_total_requests=request.max_total_requests,
        dry_run=dry_run,
        allow_network=request.allow_network,
        terms_reviewed=request.terms_reviewed,
        manual_reviewed=request.manual_reviewed,
        include_rate_limited_provider=request.include_rate_limited_provider,
        curated_official_directory_url=request.curated_official_directory_url,
        metadata_check=bool(request.metadata_check or force_metadata_check),
        max_metadata_check_requests=request.max_metadata_check_requests,
        metadata_check_top_n=request.metadata_check_top_n,
        min_source_score=request.min_source_score,
        output_dir=request.output_dir,
    )
