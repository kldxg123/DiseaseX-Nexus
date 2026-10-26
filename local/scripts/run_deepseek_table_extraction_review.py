from __future__ import annotations

import argparse
import json

from deepseek_web_search_agent.table_extraction_runner import run_structured_table_review


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run DeepSeek structured event table review.")
    parser.add_argument("--disease", required=True)
    parser.add_argument("--country-or-region", required=True)
    parser.add_argument("--date-start", required=True)
    parser.add_argument("--date-end", required=True)
    parser.add_argument("--mock-deepseek", action="store_true")
    parser.add_argument("--allow-deepseek", action="store_true")
    parser.add_argument("--allow-live-source-search", action="store_true")
    parser.add_argument("--terms-reviewed", action="store_true")
    parser.add_argument("--manual-reviewed", action="store_true")
    parser.add_argument("--search-provider", default="auto")
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
    parser.add_argument("--source-pack")
    parser.add_argument("--output-dir", default="reports/deepseek_table_review")
    args = parser.parse_args(argv)
    if args.allow_deepseek and args.mock_deepseek:
        parser.error("--allow-deepseek and --mock-deepseek cannot both be used.")
    result = run_structured_table_review(
        disease=args.disease,
        country_or_region=args.country_or_region,
        date_start=args.date_start,
        date_end=args.date_end,
        output_dir=args.output_dir,
        source_pack_path=args.source_pack,
        mock_deepseek=bool(args.mock_deepseek or not args.allow_deepseek),
        allow_deepseek=bool(args.allow_deepseek),
        allow_live_source_search=bool(args.allow_live_source_search),
        allow_network=bool(args.allow_live_source_search),
        terms_reviewed=bool(args.terms_reviewed),
        manual_reviewed=bool(args.manual_reviewed),
        providers=args.search_provider,
    )
    print(
        json.dumps(
            {
                "status": result.get("status"),
                "run_dir": result.get("run_dir"),
                "mock_rows_generated": result.get("mock_rows_generated"),
                "cleaned_rows": result.get("cleaned_rows"),
                "validation_issues_count": result.get("validation_issues_count"),
                "duplicate_groups_count": result.get("duplicate_groups_count"),
                "review_json": (result.get("paths") or {}).get("review_json"),
                "review_csv": (result.get("paths") or {}).get("review_csv"),
                "source_map": (result.get("paths") or {}).get("source_map"),
                "deepseek_called": result.get("deepseek_called"),
                "retrieval_budget": {
                    "search_provider": args.search_provider,
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
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
