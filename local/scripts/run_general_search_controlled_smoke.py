from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from common.db import get_session, init_db
from deepseek_web_search_agent import DeepSeekWebSearchRequest, run_deepseek_web_search
from deepseek_web_search_agent.cleaning_bridge import send_run_to_cleaning
from deepseek_web_search_agent.visualization_data import build_3d_visualization_dataset
from scripts.check_general_search_provider_readiness import check_readiness


def run_validation(args: argparse.Namespace) -> dict[str, Any]:
    output_root = Path(args.output_dir)
    run_dir = output_root / f"external_search_live_validation_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    run_dir.mkdir(parents=True, exist_ok=False)
    readiness = check_readiness(output_dir=str(run_dir), requested_provider=args.search_backend)
    selected_provider = str(readiness.get("selected_provider") or "mock")
    external_provider = selected_provider != "mock"
    requested_mock = str(args.search_backend or "auto").strip().lower() == "mock"

    if not requested_mock:
        if not readiness.get("can_run_live_web_search"):
            summary = _skipped_summary(args, run_dir, readiness, "missing_general_search_provider_key")
            _write_validation_reports(run_dir, summary, readiness, {}, {}, {}, {}, {}, _safety(args, external_provider=False))
            return summary
        if not (args.allow_deepseek_api and args.allow_general_web_search and args.terms_reviewed and args.manual_reviewed):
            summary = _skipped_summary(args, run_dir, readiness, "missing_controlled_live_authorization_flags")
            _write_validation_reports(run_dir, summary, readiness, {}, {}, {}, {}, {}, _safety(args, external_provider=False))
            return summary

    allow_external = bool(external_provider and args.allow_general_web_search and args.terms_reviewed and args.manual_reviewed)
    request = DeepSeekWebSearchRequest(
        disease=args.disease,
        country=args.country,
        query_intent=args.intent,
        search_backend=selected_provider if allow_external else "mock",
        allow_deepseek_api=bool(args.allow_deepseek_api and allow_external),
        allow_general_web_search=allow_external,
        allow_page_read=bool(args.allow_page_read and allow_external),
        terms_reviewed=bool(args.terms_reviewed),
        manual_reviewed=bool(args.manual_reviewed),
        max_search_queries=min(6, int(args.max_search_queries)),
        max_search_results_per_query=min(8, int(args.max_results_per_query)),
        max_pages_to_read=min(8, int(args.max_pages_to_read)) if args.allow_page_read else 0,
        max_total_http_requests=min(16, int(args.max_total_http_requests)),
        max_readable_pages_success=min(8, int(getattr(args, "max_readable_pages_success", 8))),
        stop_when_structured_candidates_found=bool(getattr(args, "stop_when_structured_candidates_found", True)),
        prefer_official_and_structured_sources=bool(getattr(args, "prefer_official_and_structured_sources", True)),
        output_dir=str(run_dir),
        database_url=args.database_url,
    ).validated()

    init_db(args.database_url)
    with get_session(args.database_url) as session:
        summary_obj = run_deepseek_web_search(request, session=session, run_cleaning=False)
        cleaning = send_run_to_cleaning(session, run_id=summary_obj.run_id)
        visualization = build_3d_visualization_dataset(session, disease=args.disease, country=args.country)

    summary = summary_obj.to_dict()
    summary.update(
        {
            "validation_dir": str(run_dir),
            "selected_provider": selected_provider,
            "external_search_provider_called": bool(allow_external),
            "mock_search_used": not allow_external,
            "raw_documents_created": cleaning.get("raw_documents_created", 0),
            "epidemic_events_created": cleaning.get("epidemic_events_created", 0),
            "feature_ready_records": cleaning.get("feature_ready_records", 0),
            "visualization_points": len(visualization.get("points") or []),
            "skipped_reason": None,
        }
    )
    _write_validation_reports(
        run_dir,
        summary,
        readiness,
        _search_summary(summary, readiness),
        _extraction_summary(summary),
        _database_summary(summary, args.database_url),
        cleaning,
        visualization,
        _safety(args, external_provider=allow_external),
    )
    return summary


def _skipped_summary(args, run_dir: Path, readiness: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "status": "skipped",
        "skipped_reason": reason,
        "disease": args.disease.lower(),
        "country": args.country,
        "search_backend": str(args.search_backend or "auto"),
        "selected_provider": readiness.get("selected_provider", "mock"),
        "external_search_provider_called": False,
        "mock_search_used": False,
        "deepseek_call_count": 0,
        "search_request_count": 0,
        "page_read_request_count": 0,
        "records_extracted": 0,
        "records_persisted": 0,
        "raw_documents_created": 0,
        "epidemic_events_created": 0,
        "feature_ready_records": 0,
        "visualization_points": 0,
        "validation_dir": str(run_dir),
        "warnings": [reason],
    }


def _search_summary(summary: dict[str, Any], readiness: dict[str, Any]) -> dict[str, Any]:
    return {
        "selected_provider": summary.get("selected_provider"),
        "external_search_provider_called": summary.get("external_search_provider_called", False),
        "mock_search_used": summary.get("mock_search_used", True),
        "search_request_count": summary.get("search_request_count", 0),
        "page_read_request_count": summary.get("page_read_request_count", 0),
        "available_providers": readiness.get("available_providers", []),
    }


def _extraction_summary(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "deepseek_call_count": summary.get("deepseek_call_count", 0),
        "records_extracted": summary.get("records_extracted", 0),
        "records_persisted": summary.get("records_persisted", 0),
        "warnings": summary.get("warnings", []),
    }


def _database_summary(summary: dict[str, Any], database_url: str) -> dict[str, Any]:
    return {
        "database_url_used": bool(database_url),
        "run_id": summary.get("run_id"),
        "records_persisted": summary.get("records_persisted", 0),
        "raw_documents_created": summary.get("raw_documents_created", 0),
        "epidemic_events_created": summary.get("epidemic_events_created", 0),
    }


def _safety(args, *, external_provider: bool) -> dict[str, Any]:
    return {
        "allow_deepseek_api": bool(args.allow_deepseek_api and external_provider),
        "allow_general_web_search": bool(args.allow_general_web_search and external_provider),
        "allow_page_read": bool(args.allow_page_read and external_provider),
        "terms_reviewed": bool(args.terms_reviewed),
        "manual_reviewed": bool(args.manual_reviewed),
        "candidate_urls_opened": False,
        "follow_links": False,
        "full_provider_response_saved": False,
        "full_page_body_saved": False,
        "full_model_input_saved": False,
        "full_model_output_saved": False,
        "credential_values_written": False,
        "source_auto_activated": False,
        "connector_auto_enabled": False,
    }


def _write_validation_reports(
    run_dir: Path,
    summary: dict[str, Any],
    readiness: dict[str, Any],
    search_summary: dict[str, Any],
    extraction_summary: dict[str, Any],
    database_summary: dict[str, Any],
    cleaning_summary: dict[str, Any],
    visualization: dict[str, Any],
    safety: dict[str, Any],
) -> None:
    payloads = {
        "external_search_live_summary.json": summary,
        "provider_readiness.json": readiness,
        "search_results_summary.json": search_summary,
        "deepseek_extraction_summary.json": extraction_summary,
        "database_persistence_summary.json": database_summary,
        "cleaning_bridge_summary.json": cleaning_summary,
        "visualization_3d_data.json": visualization,
    }
    for name, payload in payloads.items():
        (run_dir / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    _write_md(run_dir / "external_search_live_summary.md", "External Search Live Summary", summary)
    _write_md(run_dir / "provider_readiness.md", "Provider Readiness", readiness)
    _write_md(run_dir / "safety_audit.md", "Safety Audit", safety)
    (run_dir / "rollback_commands.md").write_text(
        "# Rollback Commands\n\n"
        "Review run_id before deleting local database rows.\n\n"
        "```sql\n"
        f"DELETE FROM disease_country_information_records WHERE run_id = '{summary.get('run_id', '')}';\n"
        f"DELETE FROM deepseek_web_search_results WHERE run_id = '{summary.get('run_id', '')}';\n"
        f"DELETE FROM deepseek_web_search_queries WHERE run_id = '{summary.get('run_id', '')}';\n"
        f"DELETE FROM deepseek_web_search_runs WHERE run_id = '{summary.get('run_id', '')}';\n"
        "```\n",
        encoding="utf-8",
    )


def _write_md(path: Path, title: str, payload: dict[str, Any]) -> None:
    path.write_text(
        f"# {title}\n\n```json\n{json.dumps(payload, ensure_ascii=False, indent=2, default=str)}\n```\n",
        encoding="utf-8",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run controlled general web search provider validation.")
    parser.add_argument("--disease", default="measles")
    parser.add_argument("--country", default="United States")
    parser.add_argument("--intent", default="structured_time_series")
    parser.add_argument("--search-backend", default="auto")
    parser.add_argument("--allow-deepseek-api", action="store_true")
    parser.add_argument("--allow-general-web-search", action="store_true")
    parser.add_argument("--allow-page-read", action="store_true")
    parser.add_argument("--terms-reviewed", action="store_true")
    parser.add_argument("--manual-reviewed", action="store_true")
    parser.add_argument("--max-search-queries", type=int, default=3)
    parser.add_argument("--max-results-per-query", type=int, default=5)
    parser.add_argument("--max-pages-to-read", type=int, default=0)
    parser.add_argument("--max-total-http-requests", type=int, default=6)
    parser.add_argument("--output-dir", default="reports/deepseek_web_search")
    parser.add_argument("--database-url", default="sqlite:///reports/deepseek_web_search/deepseek_web_search.sqlite")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    summary = run_validation(args)
    print(
        json.dumps(
            {
                "status": summary.get("status"),
                "skipped_reason": summary.get("skipped_reason"),
                "validation_dir": summary.get("validation_dir"),
                "selected_provider": summary.get("selected_provider"),
                "external_search_provider_called": summary.get("external_search_provider_called", False),
                "mock_search_used": summary.get("mock_search_used", False),
                "deepseek_call_count": summary.get("deepseek_call_count", 0),
                "search_request_count": summary.get("search_request_count", 0),
                "page_read_request_count": summary.get("page_read_request_count", 0),
                "records_persisted": summary.get("records_persisted", 0),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
