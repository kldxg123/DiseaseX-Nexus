from __future__ import annotations

import argparse
import json
from pathlib import Path

from common.db import get_session, init_db
from live_source_search_orchestrator.acquisition_bridge import (
    create_plan_from_run_dir,
    list_review_candidates,
    run_candidate_acquisition,
)
from live_source_search_orchestrator.event_quality_diagnostics import write_event_quality_diagnostic_report
from live_source_search_orchestrator.orchestrator import report_summary, run_live_source_search
from live_source_search_orchestrator.types import LiveSourceSearchRequest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Controlled live source search orchestrator.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    search = subparsers.add_parser("search-sources")
    search.add_argument("--country", required=True)
    search.add_argument("--disease", required=True)
    search.add_argument("--preferred-live-disease")
    search.add_argument("--providers", default="auto")
    search.add_argument("--max-total-requests", type=int, default=3)
    search.add_argument("--dry-run", action="store_true")
    search.add_argument("--allow-network", action="store_true")
    search.add_argument("--terms-reviewed", action="store_true")
    search.add_argument("--manual-reviewed", action="store_true")
    search.add_argument("--include-rate-limited-provider", action="store_true")
    search.add_argument("--curated-official-directory-url")
    search.add_argument("--metadata-check", action="store_true")
    search.add_argument("--max-metadata-check-requests", type=int, default=3)
    search.add_argument("--metadata-check-top-n", type=int, default=3)
    search.add_argument("--min-source-score", type=float, default=0.35)
    search.add_argument("--output-dir", default="reports/live_source_search")
    search.add_argument("--database-url")
    list_cmd = subparsers.add_parser("list-review-candidates")
    list_cmd.add_argument("--run-dir", required=True)
    plan_cmd = subparsers.add_parser("plan-candidate-acquisition")
    plan_cmd.add_argument("--run-dir", required=True)
    plan_cmd.add_argument("--strategy", default="top_confirmed")
    plan_cmd.add_argument("--max-requests", type=int, default=1)
    plan_cmd.add_argument("--output-dir", default="reports/live_source_search/acquisition_runs")
    run_cmd = subparsers.add_parser("run-candidate-acquisition")
    run_cmd.add_argument("--run-dir", required=True)
    run_cmd.add_argument("--strategy", default="top_confirmed")
    run_cmd.add_argument("--allow-network", action="store_true")
    run_cmd.add_argument("--terms-reviewed", action="store_true")
    run_cmd.add_argument("--manual-reviewed", action="store_true")
    run_cmd.add_argument("--max-requests", type=int, default=1)
    run_cmd.add_argument("--output-dir", default="reports/live_source_search/acquisition_runs")
    run_cmd.add_argument("--database-url")
    diagnose_cmd = subparsers.add_parser("diagnose-acquisition-quality")
    diagnose_cmd.add_argument("--run-dir", required=True)
    diagnose_cmd.add_argument("--database-url", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "search-sources":
        return _search_sources(args)
    if args.command == "list-review-candidates":
        return _list_review_candidates(args)
    if args.command == "plan-candidate-acquisition":
        return _plan_candidate_acquisition(args)
    if args.command == "run-candidate-acquisition":
        return _run_candidate_acquisition(args)
    if args.command == "diagnose-acquisition-quality":
        return _diagnose_acquisition_quality(args)
    parser.error(f"Unknown command: {args.command}")
    return 2


def _search_sources(args: argparse.Namespace) -> int:
    if int(args.max_total_requests) > 3:
        raise SystemExit("max-total-requests must be <= 3.")
    if int(args.max_metadata_check_requests) > 3:
        raise SystemExit("max-metadata-check-requests must be <= 3.")
    live_requested = bool(args.allow_network or args.terms_reviewed or args.manual_reviewed)
    dry_run = bool(args.dry_run or not live_requested)
    if not dry_run and not (args.allow_network and args.terms_reviewed and args.manual_reviewed):
        raise SystemExit("live search requires --allow-network --terms-reviewed --manual-reviewed.")
    disease = args.preferred_live_disease or args.disease
    request = LiveSourceSearchRequest(
        country=args.country,
        disease=disease,
        preferred_live_disease=args.preferred_live_disease,
        providers=args.providers,
        max_total_requests=int(args.max_total_requests),
        dry_run=dry_run,
        allow_network=bool(args.allow_network),
        terms_reviewed=bool(args.terms_reviewed),
        manual_reviewed=bool(args.manual_reviewed),
        include_rate_limited_provider=bool(args.include_rate_limited_provider),
        curated_official_directory_url=args.curated_official_directory_url,
        metadata_check=bool(args.metadata_check),
        max_metadata_check_requests=int(args.max_metadata_check_requests),
        metadata_check_top_n=int(args.metadata_check_top_n),
        min_source_score=float(args.min_source_score),
        output_dir=args.output_dir,
        database_url=args.database_url,
    )
    Path(args.output_dir).mkdir(parents=True, exist_ok=True)
    if dry_run or not args.database_url:
        report = run_live_source_search(request)
    else:
        init_db(args.database_url).dispose()
        with get_session(args.database_url) as session:
            report = run_live_source_search(request, session=session)
    print(json.dumps(report_summary(report), ensure_ascii=True, sort_keys=True))
    return 0


def _list_review_candidates(args: argparse.Namespace) -> int:
    payload = list_review_candidates(args.run_dir)
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True))
    return 0


def _plan_candidate_acquisition(args: argparse.Namespace) -> int:
    if int(args.max_requests) > 1:
        raise SystemExit("max-requests must be <= 1.")
    plan = create_plan_from_run_dir(
        args.run_dir,
        output_dir=args.output_dir,
        strategy=args.strategy,
        max_requests=int(args.max_requests),
    )
    print(json.dumps(_plan_summary(plan), ensure_ascii=True, sort_keys=True))
    return 0


def _run_candidate_acquisition(args: argparse.Namespace) -> int:
    if int(args.max_requests) > 1:
        raise SystemExit("max-requests must be <= 1.")
    if not (args.allow_network and args.terms_reviewed and args.manual_reviewed):
        raise SystemExit("candidate acquisition requires --allow-network --terms-reviewed --manual-reviewed.")
    plan = create_plan_from_run_dir(
        args.run_dir,
        output_dir=args.output_dir,
        strategy=args.strategy,
        max_requests=int(args.max_requests),
    )
    if not args.database_url:
        raise SystemExit("run-candidate-acquisition requires --database-url.")
    init_db(args.database_url).dispose()
    with get_session(args.database_url) as session:
        result = run_candidate_acquisition(
            plan,
            allow_network=True,
            terms_reviewed=True,
            manual_reviewed=True,
            session=session,
        )
    print(json.dumps(result.to_dict(), ensure_ascii=True, sort_keys=True))
    return 0


def _plan_summary(plan) -> dict:
    candidate = plan.candidate or {}
    return {
        "status": "ok" if plan.preflight.allowed else "blocked",
        "run_id": plan.run_id,
        "output_dir": plan.output_dir,
        "candidate_title": candidate.get("title"),
        "candidate_domain": candidate.get("domain"),
        "candidate_url_hash_or_safe_url_summary": (
            f"{candidate.get('domain')}:{str(candidate.get('url_hash') or '')[:12]}" if candidate else None
        ),
        "max_requests": plan.max_requests,
        "preflight": plan.preflight.to_dict(),
        "plan_path": str(Path(plan.output_dir) / "candidate_acquisition_plan.json"),
        "network_accessed": False,
        "follow_links": False,
        "files_downloaded": False,
        "full_body_saved": False,
    }


def _diagnose_acquisition_quality(args: argparse.Namespace) -> int:
    report = write_event_quality_diagnostic_report(args.run_dir, database_url=args.database_url)
    print(json.dumps(_diagnostics_summary(report), ensure_ascii=True, sort_keys=True))
    return 0


def _diagnostics_summary(report) -> dict:
    return {
        "status": report.status,
        "raw_documents_count": report.raw_documents_count,
        "epidemic_events_count": report.epidemic_events_count,
        "model_features_count": report.model_features_count,
        "feature_ready_event_count": report.event_quality.feature_ready_event_count,
        "event_quality_status": report.event_quality.quality_status,
        "blocker_types": [item.blocker_type for item in report.blockers],
        "recommended_next_action": report.recommended_next_action,
        "event_quality_diagnostics_report": report.report_paths.get("event_quality_diagnostics"),
        "feature_generation_blockers_report": report.report_paths.get("feature_generation_blockers"),
        "feature_alignment_recommendation_report": report.report_paths.get("feature_alignment_recommendation"),
    }


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
