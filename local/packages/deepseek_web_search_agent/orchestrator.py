from __future__ import annotations

from sqlalchemy.orm import Session

from common.db import get_session, init_db
from deepseek_web_search_agent.cleaning_bridge import send_run_to_cleaning
from deepseek_web_search_agent.config import get_config
from deepseek_web_search_agent.deepseek_client import DeepSeekClient
from deepseek_web_search_agent.page_reader import read_pages_safely
from deepseek_web_search_agent.persistence import (
    create_or_update_run,
    finalize_run_counts,
    persist_information_records,
    persist_query_plan,
    persist_ranked_results,
)
from deepseek_web_search_agent.query_planner import build_query_plan
from deepseek_web_search_agent.report import make_run_dir, make_run_id, write_run_reports
from deepseek_web_search_agent.result_ranker import rank_results
from deepseek_web_search_agent.search_capability_detector import choose_search_backend
from deepseek_web_search_agent.search_runner import run_search_plan
from deepseek_web_search_agent.structured_extractor import extract_information_records
from deepseek_web_search_agent.types import DeepSeekWebSearchRequest, DeepSeekWebSearchSummary
from deepseek_web_search_agent.visualization_data import build_3d_visualization_dataset
from deepseek_web_search_agent.web_search_providers import get_search_provider


def run_deepseek_web_search(
    request: DeepSeekWebSearchRequest,
    *,
    session: Session | None = None,
    run_cleaning: bool = False,
) -> DeepSeekWebSearchSummary:
    req = request.validated()
    cfg = get_config()
    capability = choose_search_backend(cfg, requested_backend=req.search_backend)
    backend = capability.provider
    run_id = make_run_id(req.disease, req.country)
    run_dir = make_run_dir(req.output_dir, run_id)
    warnings = list(capability.warnings)
    safety_summary = {
        "allow_deepseek_api": req.allow_deepseek_api,
        "allow_general_web_search": req.allow_general_web_search,
        "allow_page_read": req.allow_page_read,
        "terms_reviewed": req.terms_reviewed,
        "manual_reviewed": req.manual_reviewed,
        "max_search_queries": req.max_search_queries,
        "max_search_results_per_query": req.max_search_results_per_query,
        "max_pages_to_read": req.max_pages_to_read,
        "max_total_http_requests": req.max_total_http_requests,
        "max_readable_pages_success": req.max_readable_pages_success,
        "stop_when_structured_candidates_found": req.stop_when_structured_candidates_found,
        "prefer_official_and_structured_sources": req.prefer_official_and_structured_sources,
        "prefer_situation_reports": req.prefer_situation_reports,
        "prefer_dated_numeric_sources": req.prefer_dated_numeric_sources,
        "include_document_candidates": req.include_document_candidates,
        "do_not_download_documents": req.do_not_download_documents,
        "max_paragraph_signal_preview": req.max_paragraph_signal_preview,
        "candidate_url_follow_links": False,
        "raw_html_saved": False,
        "prompt_completion_full_text_saved": False,
    }

    def _execute(active_session: Session) -> DeepSeekWebSearchSummary:
        client = DeepSeekClient(cfg)
        plan = build_query_plan(req, client=client)
        create_or_update_run(
            active_session,
            run_id=run_id,
            request=req,
            search_backend=backend,
            status="running",
            deepseek_model=cfg.deepseek_model,
            warnings=warnings + plan.warnings,
            safety_summary=safety_summary,
        )
        persist_query_plan(active_session, run_id=run_id, plan=plan)
        provider = get_search_provider(backend, cfg)
        allow_search_network = bool(req.allow_general_web_search and req.terms_reviewed and req.manual_reviewed)
        results, search_stats = run_search_plan(
            plan,
            provider=provider,
            allow_network=allow_search_network,
            max_results_per_query=req.max_search_results_per_query,
            max_total_http_requests=req.max_total_http_requests,
        )
        ranked = rank_results(
            results,
            plan,
            prefer_official_and_structured_sources=req.prefer_official_and_structured_sources,
            prefer_dated_numeric_sources=req.prefer_dated_numeric_sources,
        )
        persist_ranked_results(active_session, run_id=run_id, ranked_results=ranked)
        page_reads, page_stats = read_pages_safely(
            ranked,
            allow_page_read=bool(req.allow_page_read and req.terms_reviewed and req.manual_reviewed),
            max_pages_to_read=req.max_pages_to_read,
            max_total_http_requests=max(0, req.max_total_http_requests - search_stats["search_request_count"]),
            max_readable_pages_success=req.max_readable_pages_success,
            stop_when_structured_candidates_found=req.stop_when_structured_candidates_found,
        )
        records, extraction_stats = extract_information_records(
            ranked,
            request=req,
            page_summaries=page_reads,
            client=client,
        )
        persisted = persist_information_records(active_session, run_id=run_id, records=records)
        cleaning_summary = (
            send_run_to_cleaning(active_session, run_id=run_id)
            if run_cleaning
            else {"raw_documents_created": 0, "epidemic_events_created": 0, "feature_ready_records": 0}
        )
        visualization = build_3d_visualization_dataset(
            active_session,
            disease=req.disease,
            country=req.country,
            mode="auto",
        )
        all_warnings = warnings + plan.warnings + list(extraction_stats.get("warnings") or [])
        finalize_run_counts(
            active_session,
            run_id=run_id,
            status="ok",
            search_query_count=len(plan.search_queries),
            search_request_count=int(search_stats["search_request_count"]),
            page_read_request_count=int(page_stats["page_read_request_count"]),
            deepseek_call_count=int(extraction_stats.get("deepseek_call_count") or 0),
            records_extracted=len(records),
            records_persisted=len(persisted),
            warnings=all_warnings,
            safety_summary=safety_summary,
        )
        summary = DeepSeekWebSearchSummary(
            status="ok",
            run_id=run_id,
            disease=req.disease.lower(),
            country=req.country,
            intent=req.query_intent,
            search_backend=backend,
            deepseek_call_count=int(extraction_stats.get("deepseek_call_count") or 0),
            search_request_count=int(search_stats["search_request_count"]),
            page_read_request_count=int(page_stats["page_read_request_count"]),
            records_extracted=len(records),
            records_persisted=len(persisted),
            raw_documents_created=int(cleaning_summary.get("raw_documents_created") or 0),
            epidemic_events_created=int(cleaning_summary.get("epidemic_events_created") or 0),
            feature_ready_records=int(cleaning_summary.get("feature_ready_records") or 0),
            visualization_points=len(visualization.get("points") or []),
            search_results_seen=int(search_stats.get("results_seen") or 0),
            readable_pages_success=int(page_stats.get("readable_pages_success") or 0),
            blocked_pages_count=int(page_stats.get("blocked_pages_count") or 0),
            structured_candidate_count=int(page_stats.get("structured_candidate_count") or 0),
            warnings=all_warnings,
        )
        paths = write_run_reports(
            run_dir=run_dir,
            summary=summary,
            query_plan=plan,
            search_results=[item.to_dict() for item in ranked],
            page_reads=[item.to_dict() for item in page_reads],
            extraction_records=[item.to_dict() for item in records],
            persistence_summary={
                "run_id": run_id,
                "records_persisted": len(persisted),
                "database_url_used": bool(req.database_url),
            },
            cleaning_summary=cleaning_summary,
            visualization_data=visualization,
            safety_audit=safety_summary | {"warnings": all_warnings},
        )
        return DeepSeekWebSearchSummary(**(summary.to_dict() | {"paths": paths}))

    if session is not None:
        return _execute(session)
    if req.database_url:
        init_db(req.database_url)
    with get_session(req.database_url) as local_session:
        return _execute(local_session)
