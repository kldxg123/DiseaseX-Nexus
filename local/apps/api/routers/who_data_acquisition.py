from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter

from apps.api.safety import sanitize_mapping
from apps.api.schemas import JobResponse, WHOApprovedIngestionRequest, WHODataAcquisitionRequest
from deepseek_web_search_agent.approval_ingestion import ingest_approved_table_rows
from deepseek_web_search_agent.who_downloadable_targeter import run_who_data_acquisition, safe_summary

router = APIRouter(prefix="/who-data-acquisition", tags=["WHO Data Acquisition"])


@router.post("/dry-run")
def who_data_acquisition_dry_run(request: WHODataAcquisitionRequest) -> JobResponse:
    result = run_who_data_acquisition(
        disease=request.disease,
        country_or_region=request.country_or_region,
        date_start=request.date_start,
        date_end=request.date_end,
        provider=request.provider,
        output_dir=request.output_dir,
        dry_run=True,
        max_provider_requests=request.max_provider_requests,
        max_metadata_checks=request.max_metadata_checks,
    )
    return JobResponse(status=result.status, summary=sanitize_mapping(safe_summary(result.to_dict()), max_items=260))


@router.post("/run")
def who_data_acquisition_controlled_run(request: WHODataAcquisitionRequest) -> JobResponse:
    if not (request.allow_live_who and request.allow_network and request.terms_reviewed and request.manual_reviewed):
        return JobResponse(
            status="blocked",
            summary={
                "status": "blocked",
                "network_accessed": False,
                "provider_request_count": 0,
                "metadata_check_request_count": 0,
                "warnings": ["controlled_live_who_requires_explicit_controls"],
                "limitations": "No WHO live request was made.",
            },
        )
    result = run_who_data_acquisition(
        disease=request.disease,
        country_or_region=request.country_or_region,
        date_start=request.date_start,
        date_end=request.date_end,
        provider=request.provider,
        output_dir=request.output_dir,
        dry_run=False,
        allow_live_who=True,
        allow_network=True,
        terms_reviewed=True,
        manual_reviewed=True,
        max_provider_requests=request.max_provider_requests,
        max_metadata_checks=request.max_metadata_checks,
    )
    return JobResponse(status=result.status, summary=sanitize_mapping(safe_summary(result.to_dict()), max_items=260))


@router.get("/reports/{run_id}")
def who_data_acquisition_report(run_id: str, output_dir: str = "reports/who_data_acquisition") -> dict:
    safe_run_id = "".join(ch for ch in str(run_id) if ch.isalnum() or ch in {"_", "-"})
    path = Path(output_dir) / safe_run_id / "who_data_acquisition_summary.json"
    if not path.exists():
        return sanitize_mapping({"status": "not_found", "run_id": safe_run_id}, max_items=120)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {"status": "failed", "warnings": ["summary_unreadable"]}
    return sanitize_mapping({"status": "ok", "run_id": safe_run_id, "summary": payload}, max_items=260)


@router.post("/ingest-approved")
def who_data_acquisition_ingest_approved(request: WHOApprovedIngestionRequest) -> JobResponse:
    result = ingest_approved_table_rows(
        review_table_path=request.review_table,
        database_url=request.database_url,
        allow_persistence=bool(request.allow_persistence),
        output_dir=request.output_dir,
    )
    return JobResponse(status=str(result.get("status") or "ok"), summary=sanitize_mapping(result, max_items=260))

