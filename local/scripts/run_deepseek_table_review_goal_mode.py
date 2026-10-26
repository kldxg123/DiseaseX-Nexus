from __future__ import annotations

import argparse
import json

from deepseek_web_search_agent.auto_target_runner import run_goal_mode


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    summary = run_goal_mode(
        targets=args.targets,
        date_start=args.date_start,
        date_end=args.date_end,
        output_dir=args.output_dir,
        database_url=args.database_url,
        mock_deepseek=bool(args.mock_deepseek),
        mock_search=bool(args.mock_search),
        allow_live_source_search=bool(args.allow_live_source_search),
        allow_deepseek=bool(args.allow_deepseek),
        terms_reviewed=bool(args.terms_reviewed),
        manual_reviewed=bool(args.manual_reviewed),
        allow_document_preview=bool(args.allow_document_preview),
        stop_when_rows_found=not bool(args.no_stop_when_rows_found),
    )
    print(json.dumps(_safe_summary(summary.to_dict()), ensure_ascii=False, sort_keys=True))
    return 0


def _safe_summary(payload: dict) -> dict:
    attempts = payload.get("attempts") if isinstance(payload.get("attempts"), list) else []
    return {
        "status": payload.get("status"),
        "successful_target": payload.get("successful_target"),
        "attempt_count": len(attempts),
        "network_accessed": payload.get("network_accessed"),
        "tavily_query_count_total": payload.get("tavily_query_count_total"),
        "pages_read_count_total": payload.get("pages_read_count_total"),
        "document_preview_count_total": payload.get("document_preview_count_total"),
        "deepseek_call_count_total": payload.get("deepseek_call_count_total"),
        "review_rows_from_live_material": payload.get("review_rows_from_live_material"),
        "attempts": [
            {
                "target": item.get("target"),
                "status": item.get("status"),
                "review_rows_from_live_material": item.get("review_rows_from_live_material"),
                "date_anchor_count": item.get("date_anchor_count"),
                "metric_anchor_count": item.get("metric_anchor_count"),
                "context_linked_candidate_count": item.get("context_linked_candidate_count"),
                "deterministic_rows_generated": item.get("deterministic_rows_generated"),
                "deepseek_rows_generated": item.get("deepseek_rows_generated"),
                "review_rows_generated": item.get("review_rows_generated"),
                "blockers": item.get("blockers"),
                "review_table": item.get("review_table"),
                "review_csv": item.get("review_csv"),
            }
            for item in attempts
        ],
        "goal_mode_summary_json": payload.get("goal_mode_summary_json"),
        "goal_mode_summary_md": payload.get("goal_mode_summary_md"),
        "warnings": payload.get("warnings") or [],
        "limitations": "Safe summary only; no prompts, completions, response bodies, raw HTML, raw text, or secrets are printed.",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run DeepSeek table review Goal Mode.")
    parser.add_argument("--targets", default="dengue:Global,measles:United States,cholera:Global,mpox:Global,Ebola:African Region")
    parser.add_argument("--date-start", required=True)
    parser.add_argument("--date-end", required=True)
    parser.add_argument("--output-dir", default="reports/deepseek_table_review_goal")
    parser.add_argument("--database-url")
    parser.add_argument("--mock-deepseek", action="store_true")
    parser.add_argument("--mock-search", action="store_true")
    parser.add_argument("--allow-live-source-search", action="store_true")
    parser.add_argument("--allow-deepseek", action="store_true")
    parser.add_argument("--terms-reviewed", action="store_true")
    parser.add_argument("--manual-reviewed", action="store_true")
    parser.add_argument("--allow-document-preview", action="store_true")
    parser.add_argument("--no-stop-when-rows-found", action="store_true")
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
