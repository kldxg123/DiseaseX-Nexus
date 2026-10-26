from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from deepseek_web_search_agent.approval_ingestion import (
    ingest_approved_table_rows,
    preview_approved_table_ingestion,
)
from deepseek_web_search_agent import DeepSeekWebSearchRequest, run_deepseek_web_search
from deepseek_web_search_agent.table_extraction_runner import run_structured_table_review
from deepseek_web_search_agent.table_visualization import build_table_visualization_data


def main() -> None:
    args = _parser().parse_args()
    source_pack_path = args.source_pack
    presearch_summary: dict[str, Any] = {"status": "not_run"}
    if args.allow_live_source_search and not source_pack_path and str(args.search_provider or "").lower() in {"tavily", "mock"}:
        presearch = run_deepseek_web_search(
            DeepSeekWebSearchRequest(
                disease=args.disease,
                country=args.country_or_region,
                query_intent="structured_numeric_evidence" if args.structured_numeric_intent else "structured_time_series",
                search_backend=str(args.search_provider or "tavily"),
                allow_deepseek_api=False,
                allow_general_web_search=bool(str(args.search_provider or "").lower() != "mock"),
                allow_page_read=bool(str(args.search_provider or "").lower() != "mock"),
                terms_reviewed=bool(args.terms_reviewed),
                manual_reviewed=bool(args.manual_reviewed),
                max_search_queries=args.max_search_queries,
                max_search_results_per_query=args.max_search_results_per_query,
                max_pages_to_read=args.max_pages_to_read,
                max_total_http_requests=args.max_total_http_requests,
                prefer_situation_reports=bool(args.prefer_situation_reports),
                prefer_dated_numeric_sources=bool(args.prefer_dated_numeric_sources),
                include_document_candidates=bool(args.include_document_candidates),
                do_not_download_documents=bool(args.do_not_download_documents),
                max_paragraph_signal_preview=args.max_paragraph_signal_preview,
                output_dir=str(Path(args.output_dir) / "_web_search_source_pack"),
                database_url=args.database_url,
            )
        )
        presearch_summary = {
            "status": presearch.status,
            "run_id": presearch.run_id,
            "search_backend": presearch.search_backend,
            "search_request_count": presearch.search_request_count,
            "search_results_seen": presearch.search_results_seen,
            "page_read_request_count": presearch.page_read_request_count,
            "readable_pages_success": presearch.readable_pages_success,
            "blocked_pages_count": presearch.blocked_pages_count,
            "structured_candidate_count": presearch.structured_candidate_count,
            "source_pack": _pick_source_pack_path(presearch.paths),
        }
        source_pack_path = presearch_summary.get("source_pack")
    table_live_source_search = bool(
        args.allow_live_source_search
        and not source_pack_path
        and str(args.search_provider or "").lower() not in {"tavily", "mock"}
    )
    result = run_structured_table_review(
        disease=args.disease,
        country_or_region=args.country_or_region,
        date_start=args.date_start,
        date_end=args.date_end,
        output_dir=args.output_dir,
        source_pack_path=source_pack_path,
        mock_deepseek=bool(args.mock_deepseek or not args.allow_deepseek),
        allow_deepseek=bool(args.allow_deepseek),
        allow_live_source_search=table_live_source_search,
        allow_network=table_live_source_search,
        terms_reviewed=bool(args.terms_reviewed),
        manual_reviewed=bool(args.manual_reviewed),
        providers=args.providers,
        max_total_provider_requests=args.max_total_provider_requests,
        metadata_check_top_n=args.metadata_check_top_n,
        max_metadata_check_requests=args.max_metadata_check_requests,
        max_sources=args.max_sources,
    )
    live_result = result
    fallback_used = False
    if not args.mock_deepseek and int(result.get("cleaned_rows") or 0) == 0:
        fallback_used = True
        result = run_structured_table_review(
            disease=args.disease,
            country_or_region=args.country_or_region,
            date_start=args.date_start,
            date_end=args.date_end,
            output_dir=args.output_dir,
            source_pack_path=args.source_pack,
            mock_deepseek=True,
            allow_deepseek=False,
            allow_live_source_search=False,
            max_sources=args.max_sources,
        )
    review_table = Path(result["paths"]["review_json"])
    approved_review_table = Path(args.approval_file) if args.approval_file else None
    approval_ingestion_executed = False
    ingestion_summary: dict[str, Any] = {"status": "not_run"}
    if (args.mock_deepseek or fallback_used) and not args.skip_mock_approval_ingestion:
        approved_review_table = _write_approved_mock_copy(review_table)
    if approved_review_table:
        preview = preview_approved_table_ingestion(review_table_path=str(approved_review_table))
        if args.database_url and preview.get("approved_rows_passed_validator", 0) > 0:
            ingestion_summary = ingest_approved_table_rows(
                review_table_path=str(approved_review_table),
                database_url=args.database_url,
                allow_persistence=True,
                output_dir=str(Path(approved_review_table).parent),
            )
            approval_ingestion_executed = True
        else:
            ingestion_summary = {"status": "preview_only", **preview}
    visualization = _read_json(Path(result["paths"]["visualization_summary"]))
    summary = {
        "status": "ok",
        "run_dir": result["run_dir"],
        "live_run_dir": live_result["run_dir"] if fallback_used else None,
        "fallback_used": fallback_used,
        "fallback_reason": "live_or_deepseek_returned_no_clean_rows" if fallback_used else None,
        "mock_e2e_completed": bool(args.mock_deepseek),
        "controlled_live_e2e_attempted": bool(args.allow_live_source_search or args.allow_deepseek),
        "network_accessed": bool(
            live_result.get("network_accessed")
            or result.get("network_accessed")
            or int(presearch_summary.get("search_request_count") or 0) > 0
        ),
        "provider_request_count_total": int(live_result.get("provider_request_count_total") or result.get("provider_request_count_total") or 0),
        "presearch_summary": presearch_summary,
        "candidate_acquisition_request_count": int(live_result.get("candidate_acquisition_request_count") or result.get("candidate_acquisition_request_count") or 0),
        "deepseek_call_count": 1 if live_result.get("deepseek_called") else 0,
        "live_rows_generated": int(live_result.get("cleaned_rows") or 0),
        "mock_rows_generated": int(result.get("mock_rows_generated") or 0),
        "cleaned_rows": int(result.get("cleaned_rows") or 0),
        "validation_issues_count": int(result.get("validation_issues_count") or 0),
        "duplicate_groups_count": int(result.get("duplicate_groups_count") or 0),
        "review_table": result["paths"]["review_json"],
        "review_csv": result["paths"]["review_csv"],
        "source_map": result["paths"]["source_map"],
        "visualization_summary": result["paths"].get("visualization_summary"),
        "approval_ingestion_executed": approval_ingestion_executed,
        "approved_rows_ingested": int(ingestion_summary.get("approved_rows_ingested") or 0),
        "epidemic_events_created": int(ingestion_summary.get("epidemic_events_created") or 0),
        "feature_generation_allowed": bool(ingestion_summary.get("feature_generation_allowed", False)),
        "ingestion_summary_path": (ingestion_summary.get("paths") or {}).get("approval_ingestion_summary"),
        "visualization_rows_total": visualization.get("rows_total"),
        "warnings": list(live_result.get("warnings") or []) + list(result.get("warnings") or []) + list(ingestion_summary.get("warnings") or []),
        "limitations": "Smoke output is a safe summary; full model instructions, model outputs, response bodies and raw page text are not printed.",
        "retrieval_budget": {
            "search_provider": args.search_provider or args.providers,
            "max_search_queries": args.max_search_queries,
            "max_search_results_per_query": args.max_search_results_per_query,
            "max_pages_to_read": args.max_pages_to_read,
            "max_total_http_requests": args.max_total_http_requests,
            "prefer_official_and_structured_sources": bool(args.prefer_official_and_structured_sources),
            "stop_when_structured_candidates_found": bool(args.stop_when_structured_candidates_found),
            "structured_numeric_intent": bool(args.structured_numeric_intent),
            "prefer_situation_reports": bool(args.prefer_situation_reports),
            "prefer_dated_numeric_sources": bool(args.prefer_dated_numeric_sources),
            "include_document_candidates": bool(args.include_document_candidates),
            "do_not_download_documents": bool(args.do_not_download_documents),
            "max_paragraph_signal_preview": args.max_paragraph_signal_preview,
        },
    }
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


def _write_approved_mock_copy(review_table: Path) -> Path:
    payload = json.loads(review_table.read_text(encoding="utf-8"))
    rows = payload.get("rows") if isinstance(payload, dict) else []
    for row in rows:
        if isinstance(row, dict) and row.get("validation_status") == "ready_for_approval":
            row["approval_status"] = "approved"
            row["reviewer_note"] = "approved by local mock smoke test"
    approved = review_table.with_name("structured_event_table_review.mock_approved.json")
    approved.write_text(json.dumps({"rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    return approved


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _pick_source_pack_path(paths: dict[str, str]) -> str | None:
    for key in (
        "structured_source_pack",
        "source_pack",
        "page_read_source_pack",
        "page_read_summary",
        "search_results_summary",
    ):
        value = paths.get(key)
        if value:
            return value
    return None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a local DeepSeek table review E2E smoke.")
    parser.add_argument("--disease", required=True)
    parser.add_argument("--country-or-region", required=True)
    parser.add_argument("--date-start", required=True)
    parser.add_argument("--date-end", required=True)
    parser.add_argument("--source-pack")
    parser.add_argument("--output-dir", default="reports/deepseek_table_review")
    parser.add_argument("--database-url")
    parser.add_argument("--mock-deepseek", action="store_true")
    parser.add_argument("--allow-live-source-search", action="store_true")
    parser.add_argument("--allow-deepseek", action="store_true")
    parser.add_argument("--terms-reviewed", action="store_true")
    parser.add_argument("--manual-reviewed", action="store_true")
    parser.add_argument("--providers", default="auto")
    parser.add_argument("--search-provider", default=None)
    parser.add_argument("--max-search-queries", type=int, default=4)
    parser.add_argument("--max-search-results-per-query", type=int, default=8)
    parser.add_argument("--max-pages-to-read", type=int, default=8)
    parser.add_argument("--max-total-http-requests", type=int, default=16)
    parser.add_argument("--prefer-official-and-structured-sources", action="store_true")
    parser.add_argument("--stop-when-structured-candidates-found", action="store_true")
    parser.add_argument("--structured-numeric-intent", action="store_true")
    parser.add_argument("--prefer-situation-reports", action="store_true")
    parser.add_argument("--prefer-dated-numeric-sources", action="store_true")
    parser.add_argument("--include-document-candidates", action="store_true")
    parser.add_argument("--do-not-download-documents", action="store_true")
    parser.add_argument("--max-paragraph-signal-preview", type=int, default=10)
    parser.add_argument("--max-total-provider-requests", type=int, default=3)
    parser.add_argument("--metadata-check-top-n", type=int, default=3)
    parser.add_argument("--max-metadata-check-requests", type=int, default=3)
    parser.add_argument("--max-sources", type=int, default=3)
    parser.add_argument("--approval-file")
    parser.add_argument("--skip-mock-approval-ingestion", action="store_true")
    return parser


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "failure_type": type(exc).__name__,
                    "safe_failure_reason": "Structured numeric table review smoke failed before a normal summary was produced.",
                    "network_accessed": "unknown",
                    "candidate_urls_accessed": False,
                    "follow_links": False,
                    "warnings": ["No raw response body, prompt, completion, or secret value is printed."],
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        raise SystemExit(1)
