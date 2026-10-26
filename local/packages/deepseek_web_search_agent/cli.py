from __future__ import annotations

import argparse
import json
from pathlib import Path

from common.db import get_session, init_db
from deepseek_web_search_agent.cleaning_bridge import send_run_to_cleaning
from deepseek_web_search_agent.persistence import list_runs
from deepseek_web_search_agent.query_planner import deterministic_query_plan
from deepseek_web_search_agent.orchestrator import run_deepseek_web_search
from deepseek_web_search_agent.types import DeepSeekWebSearchRequest
from deepseek_web_search_agent.visualization_data import build_3d_visualization_dataset


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DeepSeek general web search intelligence")
    sub = parser.add_subparsers(dest="command", required=True)
    _add_common(sub.add_parser("plan"))
    _add_common(sub.add_parser("run-mock"))
    controlled = sub.add_parser("run-controlled")
    _add_common(controlled)
    controlled.add_argument("--allow-deepseek-api", action="store_true")
    controlled.add_argument("--allow-general-web-search", action="store_true")
    controlled.add_argument("--allow-page-read", action="store_true")
    controlled.add_argument("--terms-reviewed", action="store_true")
    controlled.add_argument("--manual-reviewed", action="store_true")
    cleaning = sub.add_parser("send-to-cleaning")
    cleaning.add_argument("--run-id", required=True)
    cleaning.add_argument("--database-url", required=True)
    export = sub.add_parser("export-3d-data")
    export.add_argument("--disease", required=True)
    export.add_argument("--country", required=True)
    export.add_argument("--database-url", required=True)
    export.add_argument("--output", required=True)
    args = parser.parse_args(argv)

    if args.command == "plan":
        request = _request_from_args(args, live=False)
        plan = deterministic_query_plan(request)
        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / "query_plan.json"
        path.write_text(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        _print_safe({"status": "ok", "query_count": len(plan.search_queries), "query_plan": str(path)})
        return 0
    if args.command == "run-mock":
        request = _request_from_args(args, live=False)
        summary = run_deepseek_web_search(request, run_cleaning=False)
        _print_safe(summary.to_dict())
        return 0
    if args.command == "run-controlled":
        if not (args.allow_deepseek_api and args.allow_general_web_search and args.terms_reviewed and args.manual_reviewed):
            _print_safe({"status": "blocked", "reason": "controlled run requires allow and review flags"})
            return 2
        request = _request_from_args(args, live=True)
        summary = run_deepseek_web_search(request, run_cleaning=False)
        _print_safe(summary.to_dict())
        return 0
    if args.command == "send-to-cleaning":
        init_db(args.database_url)
        with get_session(args.database_url) as session:
            run_id = _resolve_run_id(session, args.run_id)
            result = send_run_to_cleaning(session, run_id=run_id)
        _print_safe({"status": "ok", "run_id": run_id, **result})
        return 0
    if args.command == "export-3d-data":
        init_db(args.database_url)
        with get_session(args.database_url) as session:
            payload = build_3d_visualization_dataset(
                session,
                disease=args.disease,
                country=args.country,
            )
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        _print_safe({"status": "ok", "output": str(path), "points": len(payload.get("points") or [])})
        return 0
    return 2


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--disease", required=True)
    parser.add_argument("--country", required=True)
    parser.add_argument("--intent", default="structured_time_series")
    parser.add_argument("--search-backend", default="mock")
    parser.add_argument("--max-search-queries", type=int, default=8)
    parser.add_argument("--max-results-per-query", type=int, default=8)
    parser.add_argument("--max-pages-to-read", type=int, default=8)
    parser.add_argument("--max-total-http-requests", type=int, default=20)
    parser.add_argument("--output-dir", default="reports/deepseek_web_search")
    parser.add_argument("--database-url")


def _request_from_args(args: argparse.Namespace, *, live: bool) -> DeepSeekWebSearchRequest:
    return DeepSeekWebSearchRequest(
        disease=args.disease,
        country=args.country,
        query_intent=args.intent,
        search_backend=args.search_backend if live else "mock",
        allow_deepseek_api=bool(getattr(args, "allow_deepseek_api", False)),
        allow_general_web_search=bool(getattr(args, "allow_general_web_search", False)),
        allow_page_read=bool(getattr(args, "allow_page_read", False)),
        terms_reviewed=bool(getattr(args, "terms_reviewed", False)),
        manual_reviewed=bool(getattr(args, "manual_reviewed", False)),
        max_search_queries=args.max_search_queries,
        max_search_results_per_query=args.max_results_per_query,
        max_pages_to_read=args.max_pages_to_read,
        max_total_http_requests=args.max_total_http_requests,
        output_dir=args.output_dir,
        database_url=args.database_url,
    ).validated()


def _resolve_run_id(session, value: str) -> str:
    if value != "latest":
        return value
    runs = list_runs(session, limit=1)
    if not runs:
        raise ValueError("No DeepSeek web search runs exist.")
    return str(runs[0]["run_id"])


def _print_safe(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, default=str))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
