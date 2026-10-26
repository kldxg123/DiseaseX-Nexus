from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from common.db import get_session, init_db
from deepseek_web_search_agent import DeepSeekWebSearchRequest, run_deepseek_web_search
from deepseek_web_search_agent.cleaning_bridge import send_run_to_cleaning
from deepseek_web_search_agent.config import get_config
from deepseek_web_search_agent.query_planner import deterministic_query_plan
from deepseek_web_search_agent.search_capability_detector import (
    detect_available_general_search_provider,
    detect_deepseek_native_web_search,
)
from deepseek_web_search_agent.visualization_data import build_3d_visualization_dataset


def run_smoke(args: argparse.Namespace) -> dict[str, Any]:
    cfg = get_config()
    output_root = Path(args.output_dir)
    smoke_dir = output_root / f"live_smoke_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    smoke_dir.mkdir(parents=True, exist_ok=False)
    capability = _capability_payload(cfg, requested_backend=args.search_backend)
    flags_ok = bool(args.allow_deepseek_api and args.terms_reviewed and args.manual_reviewed)

    if not cfg.has_deepseek_key:
        summary = _skip_summary(args, smoke_dir, "missing_deepseek_api_key", capability)
        _write_smoke_files(smoke_dir, summary, capability, {}, {}, {}, {}, {}, _safety(args, network=False))
        return summary
    if not flags_ok:
        summary = _skip_summary(args, smoke_dir, "missing_controlled_live_authorization_flags", capability)
        _write_smoke_files(smoke_dir, summary, capability, {}, {}, {}, {}, {}, _safety(args, network=False))
        return summary

    general_available = bool(capability["general_web_search_provider"] != "mock")
    allow_general = bool(args.allow_general_web_search and general_available)
    allow_page_read = bool(args.allow_page_read and allow_general)
    request = DeepSeekWebSearchRequest(
        disease=args.disease,
        country=args.country,
        query_intent=args.intent,
        search_backend=capability["general_web_search_provider"] if allow_general else "mock",
        allow_deepseek_api=True,
        allow_general_web_search=allow_general,
        allow_page_read=allow_page_read,
        terms_reviewed=True,
        manual_reviewed=True,
        max_search_queries=min(6, int(args.max_search_queries)),
        max_search_results_per_query=min(8, int(args.max_results_per_query)),
        max_pages_to_read=min(8, int(args.max_pages_to_read)) if allow_page_read else 0,
        max_total_http_requests=min(16, int(args.max_total_http_requests)),
        max_readable_pages_success=min(8, int(getattr(args, "max_readable_pages_success", 8))),
        stop_when_structured_candidates_found=bool(getattr(args, "stop_when_structured_candidates_found", True)),
        prefer_official_and_structured_sources=bool(getattr(args, "prefer_official_and_structured_sources", True)),
        output_dir=str(smoke_dir),
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
            "live_smoke_dir": str(smoke_dir),
            "general_web_search_skipped_reason": None if allow_general else "no_external_provider_key_or_not_allowed",
            "controlled_live_smoke": True,
            "network_accessed": bool(summary_obj.deepseek_call_count or allow_general),
            "raw_documents_created": cleaning.get("raw_documents_created", 0),
            "epidemic_events_created": cleaning.get("epidemic_events_created", 0),
            "feature_ready_records": cleaning.get("feature_ready_records", 0),
            "visualization_points": len(visualization.get("points") or []),
        }
    )
    query_plan = deterministic_query_plan(request).to_dict()
    _write_smoke_files(
        smoke_dir,
        summary,
        capability,
        _query_plan_summary(query_plan),
        _search_backend_summary(summary, capability),
        _database_summary(summary, args.database_url),
        cleaning,
        visualization,
        _safety(args, network=bool(summary["network_accessed"]), allow_general=allow_general),
    )
    return summary


def _capability_payload(cfg, *, requested_backend: str) -> dict[str, Any]:
    native = detect_deepseek_native_web_search(cfg)
    general = detect_available_general_search_provider(cfg, requested_backend=requested_backend)
    return {
        "deepseek_api_key_available": bool(cfg.has_deepseek_key),
        "deepseek_native_web_search_status": native.status,
        "deepseek_native_warnings": native.warnings,
        "general_web_search_provider": general.provider,
        "general_web_search_status": general.status,
        "general_web_search_warnings": general.warnings,
        "provider_key_available": bool(general.provider != "mock"),
    }


def _skip_summary(args, smoke_dir: Path, reason: str, capability: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "skipped",
        "skip_reason": reason,
        "disease": args.disease.lower(),
        "country": args.country,
        "intent": args.intent,
        "live_smoke_dir": str(smoke_dir),
        "deepseek_call_count": 0,
        "search_request_count": 0,
        "page_read_request_count": 0,
        "records_extracted": 0,
        "records_persisted": 0,
        "raw_documents_created": 0,
        "epidemic_events_created": 0,
        "feature_ready_records": 0,
        "visualization_points": 0,
        "capability": capability,
        "network_accessed": False,
    }


def _query_plan_summary(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "disease": plan.get("disease"),
        "country": plan.get("country"),
        "query_count": len(plan.get("search_queries") or []),
        "aliases_used": (plan.get("disease_aliases") or [])[:8],
        "generated_by": plan.get("generated_by"),
    }


def _search_backend_summary(summary: dict[str, Any], capability: dict[str, Any]) -> dict[str, Any]:
    return {
        "search_backend": summary.get("search_backend"),
        "search_request_count": summary.get("search_request_count", 0),
        "page_read_request_count": summary.get("page_read_request_count", 0),
        "general_web_search_provider": capability.get("general_web_search_provider"),
        "general_web_search_skipped_reason": summary.get("general_web_search_skipped_reason"),
    }


def _database_summary(summary: dict[str, Any], database_url: str) -> dict[str, Any]:
    return {
        "database_url_used": bool(database_url),
        "run_id": summary.get("run_id"),
        "records_persisted": summary.get("records_persisted", 0),
        "raw_documents_created": summary.get("raw_documents_created", 0),
        "epidemic_events_created": summary.get("epidemic_events_created", 0),
    }


def _safety(args, *, network: bool, allow_general: bool = False) -> dict[str, Any]:
    return {
        "network_accessed": bool(network),
        "allow_deepseek_api": bool(args.allow_deepseek_api),
        "allow_general_web_search": bool(args.allow_general_web_search and allow_general),
        "allow_page_read": bool(args.allow_page_read and allow_general),
        "terms_reviewed": bool(args.terms_reviewed),
        "manual_reviewed": bool(args.manual_reviewed),
        "full_model_input_saved": False,
        "full_model_output_saved": False,
        "full_page_body_saved": False,
        "page_text_saved": False,
        "credential_values_written": False,
        "source_auto_activated": False,
        "connector_auto_enabled": False,
    }


def _write_smoke_files(
    smoke_dir: Path,
    summary: dict[str, Any],
    capability: dict[str, Any],
    query_plan_summary: dict[str, Any],
    search_backend_summary: dict[str, Any],
    database_summary: dict[str, Any],
    cleaning_summary: dict[str, Any],
    visualization: dict[str, Any],
    safety: dict[str, Any],
) -> None:
    files = {
        "live_smoke_summary.json": summary,
        "capability_detection.json": capability,
        "deepseek_query_plan_summary.json": query_plan_summary,
        "search_backend_summary.json": search_backend_summary,
        "database_persistence_summary.json": database_summary,
        "cleaning_bridge_summary.json": cleaning_summary,
        "visualization_3d_data.json": visualization,
    }
    for name, payload in files.items():
        (smoke_dir / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    _write_md(smoke_dir / "live_smoke_summary.md", "Live Smoke Summary", summary)
    _write_md(smoke_dir / "capability_detection.md", "Capability Detection", capability)
    _write_md(smoke_dir / "safety_audit.md", "Safety Audit", safety)
    (smoke_dir / "rollback_commands.md").write_text(
        "# Rollback Commands\n\n"
        "Review the run_id before deleting rows from the local database.\n\n"
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
    parser = argparse.ArgumentParser(description="Run one guarded DeepSeek live smoke or safe skip.")
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
    parser.add_argument("--max-pages-to-read", type=int, default=2)
    parser.add_argument("--max-total-http-requests", type=int, default=8)
    parser.add_argument("--output-dir", default="reports/deepseek_web_search")
    parser.add_argument("--database-url", default="sqlite:///reports/deepseek_web_search/deepseek_web_search.sqlite")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    summary = run_smoke(args)
    print(
        json.dumps(
            {
                "status": summary.get("status"),
                "skip_reason": summary.get("skip_reason"),
                "live_smoke_dir": summary.get("live_smoke_dir"),
                "deepseek_call_count": summary.get("deepseek_call_count", 0),
                "search_request_count": summary.get("search_request_count", 0),
                "page_read_request_count": summary.get("page_read_request_count", 0),
                "records_persisted": summary.get("records_persisted", 0),
                "network_accessed": summary.get("network_accessed", False),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
