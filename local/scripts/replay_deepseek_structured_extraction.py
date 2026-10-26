from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from common.db import get_session, init_db
from deepseek_web_search_agent.deepseek_diagnostics import (
    diagnose_deepseek_config,
    run_deepseek_health_check,
    suggested_fix_for_failure,
)
from deepseek_web_search_agent.extraction_replay import run_structured_extraction_replay


def run_replay(args: argparse.Namespace) -> dict[str, Any]:
    output_root = Path(args.output_dir)
    diagnostics_dir = output_root / f"deepseek_api_diagnostics_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    diagnostics_dir.mkdir(parents=True, exist_ok=False)
    config_summary = diagnose_deepseek_config()
    health = run_deepseek_health_check(
        allow_deepseek_api=bool(args.allow_deepseek_api),
        timeout_seconds=float(args.timeout_seconds),
    )
    replay = _empty_replay(args)
    if health.get("health_check_status") == "ok":
        init_db(args.database_url)
        with get_session(args.database_url) as session:
            replay = run_structured_extraction_replay(
                session=session,
                run_dir=args.run_dir,
                allow_deepseek_api=bool(args.allow_deepseek_api),
                output_dir=str(diagnostics_dir),
                health_check=health,
            )
    else:
        replay["status"] = "skipped_due_health_check_failed"
        replay["failure_type"] = health.get("failure_type")
        replay["suggested_fix"] = health.get("suggested_fix")

    summary = _summary(config_summary, health, replay)
    _write_reports(diagnostics_dir, summary, health, replay)
    return summary | {"diagnostics_dir": str(diagnostics_dir)}


def _empty_replay(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "status": "not_run",
        "run_id": None,
        "source_run_id": None,
        "disease": None,
        "country": None,
        "deepseek_call_count": 0,
        "records_extracted_by_deepseek": 0,
        "records_persisted": 0,
        "raw_documents_created": 0,
        "epidemic_events_created": 0,
        "feature_ready_records": 0,
        "visualization_points": 0,
        "fallback_extraction_used": False,
        "failure_type": None,
        "suggested_fix": None,
        "warnings": [],
        "cleaning_summary": {},
        "visualization_data": {},
        "no_tavily_rerun": True,
        "page_read_request_count": 0,
        "source_run_dir": str(args.run_dir),
    }


def _summary(config: dict[str, Any], health: dict[str, Any], replay: dict[str, Any]) -> dict[str, Any]:
    failure_type = replay.get("failure_type") or health.get("failure_type")
    return {
        "deepseek_api_key_present": bool(config.get("api_key_present")),
        "deepseek_call_attempted": bool(health.get("deepseek_call_attempted")),
        "deepseek_call_count": int(health.get("deepseek_call_attempted") is True)
        + int(replay.get("deepseek_call_count") or 0),
        "health_check_status": health.get("health_check_status"),
        "extraction_replay_status": replay.get("status"),
        "failure_type": failure_type,
        "http_status_code": health.get("http_status_code"),
        "suggested_fix": replay.get("suggested_fix") or health.get("suggested_fix") or suggested_fix_for_failure(failure_type),
        "records_extracted_by_deepseek": int(replay.get("records_extracted_by_deepseek") or 0),
        "records_persisted": int(replay.get("records_persisted") or 0),
        "raw_documents_created": int(replay.get("raw_documents_created") or 0),
        "epidemic_events_created": int(replay.get("epidemic_events_created") or 0),
        "feature_ready_records": int(replay.get("feature_ready_records") or 0),
        "visualization_points": int(replay.get("visualization_points") or 0),
        "fallback_extraction_used": bool(replay.get("fallback_extraction_used")),
        "no_tavily_rerun": True,
        "page_read_request_count": 0,
        "warnings": list(health.get("warnings") or []) + list(replay.get("warnings") or []),
        "replay_run_id": replay.get("run_id"),
    }


def _write_reports(
    diagnostics_dir: Path,
    summary: dict[str, Any],
    health: dict[str, Any],
    replay: dict[str, Any],
) -> None:
    cleaning = replay.get("cleaning_summary") or {}
    visualization = replay.get("visualization_data") or {}
    database = {
        "run_id": replay.get("run_id"),
        "records_persisted": replay.get("records_persisted", 0),
        "raw_documents_created": replay.get("raw_documents_created", 0),
        "epidemic_events_created": replay.get("epidemic_events_created", 0),
    }
    payloads = {
        "deepseek_api_diagnostics_summary.json": summary,
        "deepseek_health_check.json": health,
        "deepseek_structured_extraction_replay.json": replay,
        "database_persistence_summary.json": database,
        "cleaning_bridge_summary.json": cleaning,
        "visualization_3d_data.json": visualization,
    }
    for name, payload in payloads.items():
        (diagnostics_dir / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    _write_md(diagnostics_dir / "deepseek_api_diagnostics_summary.md", "DeepSeek API Diagnostics Summary", summary)
    _write_md(diagnostics_dir / "deepseek_structured_extraction_replay.md", "DeepSeek Structured Extraction Replay", replay)
    _write_md(
        diagnostics_dir / "safety_audit.md",
        "Safety Audit",
        {
            "no_tavily_rerun": True,
            "general_web_search_provider_called": False,
            "candidate_urls_opened": False,
            "follow_links": False,
            "page_read_request_count": 0,
            "full_page_body_saved": False,
            "full_model_input_saved": False,
            "full_model_output_saved": False,
            "credential_values_written": False,
        },
    )
    (diagnostics_dir / "rollback_commands.md").write_text(
        "# Rollback Commands\n\n"
        "Review run_id before deleting local database rows.\n\n"
        "```sql\n"
        f"DELETE FROM disease_country_information_records WHERE run_id = '{summary.get('replay_run_id') or ''}';\n"
        f"DELETE FROM deepseek_web_search_results WHERE run_id = '{summary.get('replay_run_id') or ''}';\n"
        f"DELETE FROM deepseek_web_search_runs WHERE run_id = '{summary.get('replay_run_id') or ''}';\n"
        "```\n",
        encoding="utf-8",
    )


def _write_md(path: Path, title: str, payload: dict[str, Any]) -> None:
    path.write_text(
        f"# {title}\n\n```json\n{json.dumps(payload, ensure_ascii=False, indent=2, default=str)}\n```\n",
        encoding="utf-8",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Replay DeepSeek structured extraction from existing safe search summaries.")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--allow-deepseek-api", action="store_true")
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--output-dir", default="reports/deepseek_web_search")
    parser.add_argument("--timeout-seconds", type=float, default=20.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    summary = run_replay(args)
    print(
        json.dumps(
            {
                "status": summary.get("extraction_replay_status"),
                "diagnostics_dir": summary.get("diagnostics_dir"),
                "deepseek_call_count": summary.get("deepseek_call_count", 0),
                "health_check_status": summary.get("health_check_status"),
                "failure_type": summary.get("failure_type"),
                "records_persisted": summary.get("records_persisted", 0),
                "fallback_extraction_used": summary.get("fallback_extraction_used", False),
                "no_tavily_rerun": True,
                "page_read_request_count": 0,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
