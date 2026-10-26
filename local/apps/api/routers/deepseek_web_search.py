from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from apps.api.deps import get_db_session
from apps.api.safety import sanitize_mapping
from apps.api.schemas import (
    DeepSeekAnnotationDraftValidationRequest,
    DeepSeekAnnotationWorkspaceRequest,
    DeepSeekApiDiagnoseRequest,
    DeepSeekCleaningRequest,
    DeepSeekExtractionAuditRequest,
    DeepSeekHumanReviewImportRequest,
    DeepSeekHumanReviewPromotionReportRequest,
    DeepSeekHumanReviewTemplateExportRequest,
    DeepSeekReadinessReviewExportRequest,
    DeepSeekReplayExtractionRequest,
    DeepSeekTableReviewIngestRequest,
    DeepSeekTableReviewGoalModeRequest,
    DeepSeekTableReviewRequest,
    DeepSeekTableReviewValidateRequest,
    DeepSeekVisualizationRequest,
    DeepSeekWebSearchRequestModel,
    JobResponse,
)
from deepseek_web_search_agent import (
    DeepSeekWebSearchRequest,
    build_3d_visualization_dataset,
    deterministic_query_plan,
    diagnose_deepseek_config,
    run_deepseek_web_search,
    run_deepseek_health_check,
    run_structured_extraction_replay,
    send_run_to_cleaning,
    build_readiness_review_rows,
    export_human_review_template,
    export_human_review_queue,
    build_annotation_workspace_rows,
    create_empty_annotation_draft,
    export_annotation_workspace_bundle,
    preview_promotion_decisions,
    validate_annotation_draft,
    import_human_review_annotations,
    promote_reviewed_records_to_epidemic_events,
    summarize_readiness_review,
    write_deepseek_quality_audit_report,
    write_readiness_review_reports,
    build_disease_time_query_request,
    build_source_pack,
    build_table_query_source_pack,
    build_table_visualization_data,
    ingest_approved_table_rows,
    preview_approved_table_ingestion,
    run_structured_table_review,
    validate_extracted_table_rows,
)
from pathlib import Path
import json
from deepseek_web_search_agent.persistence import list_records, list_runs
from deepseek_web_search_agent.auto_target_runner import run_goal_mode
from deepseek_web_search_agent.source_pack_builder import source_pack_to_dict
from deepseek_web_search_agent.staging_review import approval_status_summary, load_review_table

router = APIRouter(prefix="/deepseek-web-search", tags=["DeepSeek Web Search"])


@router.post("/diagnose-api")
def diagnose_deepseek_api(request: DeepSeekApiDiagnoseRequest) -> dict:
    config_summary = diagnose_deepseek_config()
    health = run_deepseek_health_check(
        allow_deepseek_api=bool(request.allow_deepseek_api),
        timeout_seconds=float(request.timeout_seconds),
    )
    return sanitize_mapping(
        {
            "status": health.get("health_check_status"),
            "config": config_summary,
            "health": health,
            "limitations": "No credential values, full prompts, or completions are returned.",
        },
        max_items=160,
    )


@router.post("/replay-extraction")
def replay_deepseek_extraction(
    request: DeepSeekReplayExtractionRequest,
    session: Session = Depends(get_db_session),
) -> JobResponse:
    replay = run_structured_extraction_replay(
        session=session,
        run_dir=request.run_dir,
        allow_deepseek_api=bool(request.allow_deepseek_api),
        output_dir=request.output_dir,
    )
    return JobResponse(status=str(replay.get("status") or "ok"), summary=sanitize_mapping(replay, max_items=220))


@router.get("/diagnostics/{run_id}")
def get_deepseek_diagnostics(run_id: str, session: Session = Depends(get_db_session)) -> dict:
    records = list_records(session, run_id=run_id, limit=500)
    return sanitize_mapping(
        {
            "run_id": run_id,
            "records": len(records),
            "cleaning_status_counts": _count_record_statuses(records),
            "limitations": "Diagnostics are local summaries only; no raw model or page payload is returned.",
        },
        max_items=160,
    )


@router.post("/extraction-quality-audit")
def audit_deepseek_extraction_quality(request: DeepSeekExtractionAuditRequest) -> JobResponse:
    try:
        summary = write_deepseek_quality_audit_report(
            request.report_dir,
            database_url=request.database_url,
        )
    except Exception as exc:
        summary = {
            "status": "failed",
            "failure_type": type(exc).__name__,
            "deepseek_called_in_this_step": False,
            "tavily_rerun_in_this_step": False,
            "candidate_urls_accessed": False,
            "report_dir": request.report_dir,
            "warnings": [
                "DeepSeek extraction audit could not be completed from local report files.",
                "No DeepSeek call, search rerun, page read, or candidate URL visit was performed.",
            ],
            "limitations": "Audit failure is reported as a safe summary; raw exception details are not returned.",
        }
    return JobResponse(status=str(summary.get("status") or "ok"), summary=sanitize_mapping(summary, max_items=220))


@router.get("/readiness-review/summary")
def deepseek_readiness_review_summary(
    report_dir: str = Query(default="reports/deepseek_web_search/deepseek_api_diagnostics_20260708T022808Z"),
) -> dict:
    rows = build_readiness_review_rows(report_dir)
    summary = summarize_readiness_review(rows).to_dict()
    return sanitize_mapping(summary, max_items=220)


@router.get("/readiness-review/table")
def deepseek_readiness_review_table(
    report_dir: str = Query(default="reports/deepseek_web_search/deepseek_api_diagnostics_20260708T022808Z"),
    readiness: str | None = Query(default=None),
    blocker: str | None = Query(default=None),
    visualization_ready: bool | None = Query(default=None),
    requires_human_review: bool | None = Query(default=None),
) -> dict:
    from deepseek_web_search_agent import filter_readiness_rows

    rows = build_readiness_review_rows(report_dir)
    rows = filter_readiness_rows(
        rows,
        readiness=readiness,
        blocker=blocker,
        visualization_ready=visualization_ready,
        requires_human_review=requires_human_review,
    )
    return sanitize_mapping({"records": [row.to_dict() for row in rows]}, max_items=500)


@router.get("/readiness-review/manual-review-queue")
def deepseek_readiness_manual_review_queue(
    report_dir: str = Query(default="reports/deepseek_web_search/deepseek_api_diagnostics_20260708T022808Z"),
) -> dict:
    rows = [row for row in build_readiness_review_rows(report_dir) if row.requires_human_review]
    return sanitize_mapping({"queue": [row.to_dict() for row in rows]}, max_items=500)


@router.post("/readiness-review/export")
def deepseek_readiness_review_export(request: DeepSeekReadinessReviewExportRequest) -> JobResponse:
    if request.output_dir:
        rows = build_readiness_review_rows(request.report_dir)
        export = export_human_review_queue(rows, request.output_dir)
        summary = export.to_dict()
    else:
        summary = write_readiness_review_reports(request.report_dir)
    return JobResponse(status=str(summary.get("status") or "ok"), summary=sanitize_mapping(summary, max_items=220))


@router.get("/human-review/template")
def deepseek_human_review_template(
    report_dir: str = Query(default="reports/deepseek_web_search/deepseek_api_diagnostics_20260708T022808Z"),
) -> dict:
    rows = build_readiness_review_rows(report_dir)
    payload = {
        "template_rows": [
            {
                "record_id": row.record_id,
                "disease": row.disease,
                "country": row.country,
                "source_domain": row.source_domain,
                "source_title_summary": row.source_title_summary,
                "evidence_summary": row.evidence_summary,
                "readiness_class": row.readiness_class,
                "missing_fields": row.missing_fields,
                "blocker_types": row.blocker_types,
                "reviewer_decision": "",
                "confirmed_event_date": "",
                "confirmed_iso_week": "",
                "metric_type": "",
                "metric_value": "",
                "source_grounding_confirmed": False,
            }
            for row in rows
        ],
        "limitations": "Template only; no persistence and no network call.",
    }
    return sanitize_mapping(payload, max_items=500)


@router.post("/human-review/export-template")
def deepseek_human_review_export_template(request: DeepSeekHumanReviewTemplateExportRequest) -> JobResponse:
    summary = export_human_review_template(request.report_dir, request.output_dir)
    return JobResponse(status=str(summary.get("status") or "ok"), summary=sanitize_mapping(summary, max_items=220))


@router.post("/human-review/import-annotations")
def deepseek_human_review_import_annotations(request: DeepSeekHumanReviewImportRequest) -> JobResponse:
    report = promote_reviewed_records_to_epidemic_events(
        report_dir=request.report_dir,
        annotation_file=request.annotation_file,
        output_dir=request.output_dir,
        database_url=request.database_url,
        allow_persistence=bool(request.allow_persistence),
    )
    return JobResponse(status=report.status, summary=sanitize_mapping(report.to_dict(), max_items=260))


@router.post("/human-review/promote-events")
def deepseek_human_review_promote_events(request: DeepSeekHumanReviewImportRequest) -> JobResponse:
    report = promote_reviewed_records_to_epidemic_events(
        report_dir=request.report_dir,
        annotation_file=request.annotation_file,
        output_dir=request.output_dir,
        database_url=request.database_url,
        allow_persistence=bool(request.allow_persistence),
    )
    return JobResponse(status=report.status, summary=sanitize_mapping(report.to_dict(), max_items=260))


@router.get("/human-review/promotion-report")
def deepseek_human_review_promotion_report(
    output_dir: str = Query(default="reports/deepseek_web_search/deepseek_api_diagnostics_20260708T022808Z/human_review"),
) -> dict:
    root = Path(output_dir)
    summary = _read_local_json(root / "event_promotion_summary.json")
    feature = _read_local_json(root / "feature_eligibility_after_promotion.json")
    return sanitize_mapping({"summary": summary, "feature_eligibility": feature}, max_items=220)


@router.get("/annotation-workspace/rows")
def deepseek_annotation_workspace_rows(
    report_dir: str = Query(default="reports/deepseek_web_search/deepseek_api_diagnostics_20260708T022808Z"),
) -> dict:
    rows = build_annotation_workspace_rows(report_dir)
    return sanitize_mapping({"rows": [row.to_dict() for row in rows]}, max_items=500)


@router.post("/annotation-workspace/export-template")
def deepseek_annotation_workspace_export_template(request: DeepSeekAnnotationWorkspaceRequest) -> JobResponse:
    result = create_empty_annotation_draft(request.report_dir, request.output_dir)
    return JobResponse(status=result.status, summary=sanitize_mapping(result.to_dict(), max_items=160))


@router.post("/annotation-workspace/validate")
def deepseek_annotation_workspace_validate(request: DeepSeekAnnotationDraftValidationRequest) -> dict:
    result = validate_annotation_draft(request.annotation_file)
    return sanitize_mapping(result.to_dict(), max_items=260)


@router.post("/annotation-workspace/preview-promotion")
def deepseek_annotation_workspace_preview_promotion(request: DeepSeekAnnotationDraftValidationRequest) -> dict:
    result = preview_promotion_decisions(request.annotation_file)
    return sanitize_mapping(result.to_dict(), max_items=260)


@router.post("/annotation-workspace/export-bundle")
def deepseek_annotation_workspace_export_bundle(request: DeepSeekAnnotationWorkspaceRequest) -> JobResponse:
    result = export_annotation_workspace_bundle(request.report_dir, request.output_dir)
    return JobResponse(status=str(result.get("status") or "ok"), summary=sanitize_mapping(result, max_items=220))


@router.post("/table-review/build-source-pack")
def deepseek_table_review_build_source_pack(request: DeepSeekTableReviewRequest) -> dict:
    orchestration = build_table_query_source_pack(
        disease=request.disease,
        country_or_region=request.country_or_region,
        date_start=request.date_start,
        date_end=request.date_end,
        max_sources=request.max_sources,
        source_pack_path=request.source_pack,
        allow_live_source_search=bool(request.allow_live_source_search),
        allow_network=bool(request.allow_network),
        terms_reviewed=bool(request.terms_reviewed),
        manual_reviewed=bool(request.manual_reviewed),
        providers=request.providers,
        max_total_provider_requests=request.max_total_provider_requests,
        metadata_check_top_n=request.metadata_check_top_n,
        max_metadata_check_requests=request.max_metadata_check_requests,
        output_dir=request.output_dir,
    )
    return sanitize_mapping(
        {
            "status": "ok",
            "query": orchestration.query_request.to_dict(),
            "source_count": len(orchestration.sources),
            **source_pack_to_dict(orchestration.sources),
            "source_search_summary": orchestration.source_search_summary,
            "provider_request_count_total": orchestration.provider_request_count_total,
            "network_accessed": orchestration.network_accessed,
            "deepseek_called": False,
            "limitations": "Source pack contains safe summaries only; no full page body is returned.",
        },
        max_items=260,
    )


@router.post("/table-review/run-mock")
def deepseek_table_review_run_mock(request: DeepSeekTableReviewRequest) -> JobResponse:
    result = run_structured_table_review(
        disease=request.disease,
        country_or_region=request.country_or_region,
        date_start=request.date_start,
        date_end=request.date_end,
        output_dir=request.output_dir,
        source_pack_path=request.source_pack,
        mock_deepseek=True,
        allow_deepseek=False,
        allow_live_source_search=bool(request.allow_live_source_search),
        allow_network=bool(request.allow_network),
        terms_reviewed=bool(request.terms_reviewed),
        manual_reviewed=bool(request.manual_reviewed),
        providers=request.providers,
        max_total_provider_requests=request.max_total_provider_requests,
        metadata_check_top_n=request.metadata_check_top_n,
        max_metadata_check_requests=request.max_metadata_check_requests,
        max_sources=request.max_sources,
    )
    return JobResponse(status=str(result.get("status") or "ok"), summary=sanitize_mapping(_table_review_api_summary(result), max_items=300))


@router.post("/table-review/run-deepseek")
def deepseek_table_review_run_deepseek(request: DeepSeekTableReviewRequest) -> JobResponse:
    if not request.allow_deepseek:
        return JobResponse(
            status="blocked",
            summary={"status": "blocked", "deepseek_called": False, "warnings": ["allow_deepseek_required"]},
        )
    result = run_structured_table_review(
        disease=request.disease,
        country_or_region=request.country_or_region,
        date_start=request.date_start,
        date_end=request.date_end,
        output_dir=request.output_dir,
        source_pack_path=request.source_pack,
        mock_deepseek=False,
        allow_deepseek=True,
        allow_live_source_search=bool(request.allow_live_source_search),
        allow_network=bool(request.allow_network),
        terms_reviewed=bool(request.terms_reviewed),
        manual_reviewed=bool(request.manual_reviewed),
        providers=request.providers,
        max_total_provider_requests=request.max_total_provider_requests,
        metadata_check_top_n=request.metadata_check_top_n,
        max_metadata_check_requests=request.max_metadata_check_requests,
        max_sources=request.max_sources,
    )
    return JobResponse(status=str(result.get("status") or "ok"), summary=sanitize_mapping(_table_review_api_summary(result), max_items=300))


@router.post("/table-review/goal-mode")
def deepseek_table_review_goal_mode(request: DeepSeekTableReviewGoalModeRequest) -> JobResponse:
    live_allowed = bool(request.allow_live_source_search and request.terms_reviewed and request.manual_reviewed)
    summary = run_goal_mode(
        targets=request.targets,
        date_start=request.date_start,
        date_end=request.date_end,
        output_dir=request.output_dir,
        database_url=request.database_url,
        mock_deepseek=bool(request.mock_deepseek),
        mock_search=bool(request.mock_search),
        allow_live_source_search=live_allowed,
        allow_deepseek=bool(request.allow_deepseek and request.terms_reviewed and request.manual_reviewed),
        terms_reviewed=request.terms_reviewed,
        manual_reviewed=request.manual_reviewed,
        allow_document_preview=request.allow_document_preview,
        stop_when_rows_found=request.stop_when_rows_found,
    )
    return JobResponse(status=summary.status, summary=sanitize_mapping(summary.to_dict(), max_items=420), warnings=summary.warnings)


@router.get("/table-review/goal-mode/{run_id}")
def deepseek_table_review_goal_mode_summary(
    run_id: str,
    output_dir: str = Query(default="reports/deepseek_table_review_goal"),
) -> dict:
    root = Path(output_dir)
    summary_path = root / run_id / "goal_mode_summary.json"
    if not summary_path.exists():
        summary_path = root / "goal_mode_summary.json"
    return sanitize_mapping(
        {
            "status": "ok" if summary_path.exists() else "not_found",
            "run_id": run_id,
            "summary": _read_local_json(summary_path),
        },
        max_items=500,
    )


@router.post("/table-review/validate")
def deepseek_table_review_validate(request: DeepSeekTableReviewValidateRequest) -> dict:
    review_path = Path(request.review_table)
    rows = load_review_table(review_path)
    query_payload = _read_local_json(review_path.parent / "query_request.json")
    query = build_disease_time_query_request(
        disease=str(query_payload.get("disease") or "unknown"),
        country_or_region=str(query_payload.get("country_or_region") or "unknown"),
        date_start=str(query_payload.get("date_start") or "1900-01-01"),
        date_end=str(query_payload.get("date_end") or "2999-12-31"),
    )
    sources = build_source_pack(query, source_pack_path=str(review_path.parent / "source_map.json"))
    validated, issues = validate_extracted_table_rows(rows, query, sources)
    if not validated:
        existing_issues = _read_local_json(review_path.parent / "validation_issues.json")
        bundle_issues = existing_issues.get("issues") if isinstance(existing_issues.get("issues"), list) else []
        issues = bundle_issues or issues
    return sanitize_mapping({"status": "ok", "rows": validated, "issues": issues, "issues_count": len(issues)}, max_items=500)


@router.post("/table-review/clean-validate")
def deepseek_table_review_clean_validate(request: DeepSeekTableReviewValidateRequest) -> dict:
    review_path = Path(request.review_table)
    rows = load_review_table(review_path)
    query_payload = _read_local_json(review_path.parent / "query_request.json")
    query = build_disease_time_query_request(
        disease=str(query_payload.get("disease") or "unknown"),
        country_or_region=str(query_payload.get("country_or_region") or "unknown"),
        date_start=str(query_payload.get("date_start") or "1900-01-01"),
        date_end=str(query_payload.get("date_end") or "2999-12-31"),
    )
    sources = build_source_pack(query, source_pack_path=str(review_path.parent / "source_map.json"))
    validated, issues = validate_extracted_table_rows(rows, query, sources)
    if not validated:
        existing_issues = _read_local_json(review_path.parent / "validation_issues.json")
        bundle_issues = existing_issues.get("issues") if isinstance(existing_issues.get("issues"), list) else []
        issues = bundle_issues or issues
    visualization = build_table_visualization_data(validated, sources)
    return sanitize_mapping(
        {
            "status": "ok",
            "rows": validated,
            "issues": issues,
            "issues_count": len(issues),
            "duplicate_groups_path": str(review_path.parent / "duplicate_report.json"),
            "approval_status_summary": approval_status_summary(validated),
            "visualization": visualization,
            "limitations": "Clean/validate uses local staging rows only; no persistence is performed.",
        },
        max_items=900,
    )


@router.post("/table-review/export-review-table")
def deepseek_table_review_export_review_table(request: DeepSeekTableReviewValidateRequest) -> dict:
    review_path = Path(request.review_table)
    root = review_path.parent
    rows = load_review_table(review_path)
    return sanitize_mapping(
        {
            "status": "ok" if review_path.exists() else "not_found",
            "rows_count": len(rows),
            "review_json": str(root / "structured_event_table_review.json"),
            "review_csv": str(root / "structured_event_table_review.csv"),
            "review_markdown": str(root / "structured_event_table_review.md"),
            "source_map": str(root / "source_map.json"),
            "validation_issues": str(root / "validation_issues.json"),
            "duplicate_report": str(root / "duplicate_report.json"),
            "visualization_summary": str(root / "visualization_summary.json"),
            "safety_audit": str(root / "safety_audit.md"),
        },
        max_items=180,
    )


@router.post("/table-review/preview-ingestion")
def deepseek_table_review_preview_ingestion(request: DeepSeekTableReviewValidateRequest) -> dict:
    result = preview_approved_table_ingestion(review_table_path=request.review_table)
    return sanitize_mapping(result, max_items=220)


@router.post("/table-review/ingest-approved")
def deepseek_table_review_ingest_approved(request: DeepSeekTableReviewIngestRequest) -> JobResponse:
    result = ingest_approved_table_rows(
        review_table_path=request.review_table,
        database_url=request.database_url,
        allow_persistence=bool(request.allow_persistence),
        output_dir=request.output_dir,
    )
    return JobResponse(status=str(result.get("status") or "ok"), summary=sanitize_mapping(result, max_items=260))


@router.get("/table-review/{run_id}")
def deepseek_table_review_get(run_id: str, output_dir: str = Query(default="reports/deepseek_table_review")) -> dict:
    run_dir = Path(output_dir) / run_id
    rows = load_review_table(run_dir / "structured_event_table_review.json")
    summary = _read_local_json(run_dir / "deepseek_table_extraction_summary.json")
    return sanitize_mapping(
        {
            "status": "ok" if run_dir.exists() else "not_found",
            "run_id": run_id,
            "run_dir": str(run_dir),
            "rows": rows,
            "summary": summary,
        },
        max_items=500,
    )


@router.get("/table-review/visualization/{run_id}")
def deepseek_table_review_visualization(run_id: str, output_dir: str = Query(default="reports/deepseek_table_review")) -> dict:
    run_dir = Path(output_dir) / run_id
    return sanitize_mapping(
        {
            "status": "ok" if run_dir.exists() else "not_found",
            "run_id": run_id,
            "visualization_summary": _read_local_json(run_dir / "visualization_summary.json"),
            "time_series_data": _read_local_json_any(run_dir / "time_series_data.json"),
            "country_region_summary": _read_local_json_any(run_dir / "country_region_summary.json"),
            "source_contribution_summary": _read_local_json_any(run_dir / "source_contribution_summary.json"),
            "metric_totals_summary": _read_local_json(run_dir / "metric_totals_summary.json"),
            "optional_3d_visualization_data": _read_local_json(run_dir / "optional_3d_visualization_data.json"),
            "limitations": "Visualization uses cleaned staging rows only; no coordinates are fabricated.",
        },
        max_items=1000,
    )


@router.post("/plan")
def plan_deepseek_web_search(request: DeepSeekWebSearchRequestModel) -> dict:
    plan = deterministic_query_plan(_to_request(request, force_mock=True))
    return {
        "status": "ok",
        "query_plan": sanitize_mapping(plan.to_dict(), max_items=200),
        "limitations": "Plan-only endpoint; no DeepSeek API call and no network search are executed.",
    }


@router.post("/run")
def run_deepseek_web_search_endpoint(
    request: DeepSeekWebSearchRequestModel,
    session: Session = Depends(get_db_session),
) -> JobResponse:
    req = _to_request(request, force_mock=not request.allow_general_web_search)
    if request.allow_general_web_search and not (
        request.allow_deepseek_api and request.terms_reviewed and request.manual_reviewed
    ):
        req = _to_request(request, force_mock=True)
    summary = run_deepseek_web_search(req, session=session, run_cleaning=False)
    return JobResponse(
        status=summary.status,
        summary=sanitize_mapping(summary.to_dict(), max_items=220),
        warnings=summary.warnings,
    )


@router.get("/runs")
def deepseek_web_search_runs(
    limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_db_session),
) -> dict:
    return {"runs": sanitize_mapping(list_runs(session, limit=limit), max_items=200)}


@router.get("/records")
def deepseek_web_search_records(
    disease: str | None = Query(default=None),
    country: str | None = Query(default=None),
    run_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    session: Session = Depends(get_db_session),
) -> dict:
    return {
        "records": sanitize_mapping(
            list_records(session, disease=disease, country=country, run_id=run_id, limit=limit),
            max_items=500,
        )
    }


@router.post("/send-to-cleaning")
def deepseek_web_search_send_to_cleaning(
    request: DeepSeekCleaningRequest,
    session: Session = Depends(get_db_session),
) -> JobResponse:
    summary = send_run_to_cleaning(session, run_id=request.run_id)
    return JobResponse(status="ok", summary=sanitize_mapping(summary, max_items=100))


@router.get("/cleaning-status/{run_id}")
def deepseek_web_search_cleaning_status(run_id: str, session: Session = Depends(get_db_session)) -> dict:
    records = list_records(session, run_id=run_id, limit=500)
    by_status: dict[str, int] = {}
    for record in records:
        status = str(record.get("cleaning_status") or "unknown")
        by_status[status] = by_status.get(status, 0) + 1
    return {"run_id": run_id, "by_status": sanitize_mapping(by_status, max_items=100), "records": len(records)}


@router.get("/visualization/3d-data")
def deepseek_web_search_3d_data(
    disease: str,
    country: str,
    mode: str = "auto",
    session: Session = Depends(get_db_session),
) -> dict:
    payload = build_3d_visualization_dataset(session, disease=disease, country=country, mode=mode)
    return sanitize_mapping(payload, max_items=1000)


@router.post("/visualization/3d-data")
def deepseek_web_search_3d_data_post(
    request: DeepSeekVisualizationRequest,
    session: Session = Depends(get_db_session),
) -> dict:
    payload = build_3d_visualization_dataset(
        session,
        disease=request.disease,
        country=request.country,
        mode=request.mode,
    )
    return sanitize_mapping(payload, max_items=1000)


def _count_record_statuses(records: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        status = str(record.get("cleaning_status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return counts


def _read_local_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _read_local_json_any(path: Path):
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []


def _table_review_api_summary(result: dict) -> dict:
    paths = result.get("paths") if isinstance(result.get("paths"), dict) else {}
    return {
        "status": result.get("status"),
        "run_id": result.get("run_id"),
        "run_dir": result.get("run_dir"),
        "disease": result.get("disease"),
        "country_or_region": result.get("country_or_region"),
        "date_start": result.get("date_start"),
        "date_end": result.get("date_end"),
        "mock_deepseek": result.get("mock_deepseek"),
        "deepseek_called": result.get("deepseek_called"),
        "source_count": result.get("source_count"),
        "network_accessed": result.get("network_accessed"),
        "provider_request_count_total": result.get("provider_request_count_total"),
        "candidate_acquisition_request_count": result.get("candidate_acquisition_request_count"),
        "source_search_summary": result.get("source_search_summary"),
        "mock_rows_generated": result.get("mock_rows_generated"),
        "cleaned_rows": result.get("cleaned_rows"),
        "validation": result.get("validation"),
        "validation_issues_count": result.get("validation_issues_count"),
        "duplicate_groups_count": result.get("duplicate_groups_count"),
        "review_json": paths.get("review_json"),
        "review_csv": paths.get("review_csv"),
        "source_map": paths.get("source_map"),
        "paths": paths,
        "warnings": result.get("warnings", []),
        "limitations": result.get("limitations"),
    }


def _to_request(request: DeepSeekWebSearchRequestModel, *, force_mock: bool = False) -> DeepSeekWebSearchRequest:
    return DeepSeekWebSearchRequest(
        disease=request.disease,
        country=request.country,
        query_intent=request.query_intent,
        search_backend="mock" if force_mock else request.search_backend,
        allow_deepseek_api=bool(request.allow_deepseek_api and not force_mock),
        allow_general_web_search=bool(request.allow_general_web_search and not force_mock),
        allow_page_read=bool(request.allow_page_read and not force_mock),
        terms_reviewed=request.terms_reviewed,
        manual_reviewed=request.manual_reviewed,
        max_search_queries=request.max_search_queries,
        max_search_results_per_query=request.max_search_results_per_query,
        max_pages_to_read=request.max_pages_to_read,
        max_total_http_requests=request.max_total_http_requests,
        max_readable_pages_success=request.max_readable_pages_success,
        stop_when_structured_candidates_found=request.stop_when_structured_candidates_found,
        prefer_official_and_structured_sources=request.prefer_official_and_structured_sources,
        prefer_situation_reports=request.prefer_situation_reports,
        prefer_dated_numeric_sources=request.prefer_dated_numeric_sources,
        include_document_candidates=request.include_document_candidates,
        do_not_download_documents=request.do_not_download_documents,
        max_paragraph_signal_preview=request.max_paragraph_signal_preview,
        output_dir=request.output_dir,
    ).validated()
