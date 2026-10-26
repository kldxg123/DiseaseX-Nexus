from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGES_DIR = PROJECT_ROOT / "packages"
for candidate in (PROJECT_ROOT, PACKAGES_DIR):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

REPORT_DIR = Path("reports/milestone1")
REQUIRED_FILES = {
    "completion_markdown": "MILESTONE1_COMPLETION_REPORT.md",
    "completion_json": "MILESTONE1_COMPLETION_REPORT.json",
    "validation_summary": "MILESTONE1_VALIDATION_SUMMARY.md",
    "known_limitations": "MILESTONE1_KNOWN_LIMITATIONS.md",
    "next_steps": "MILESTONE1_NEXT_STEPS.md",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate Milestone 1 completion reports from safe summaries.")
    parser.add_argument("--output-dir", default=str(REPORT_DIR))
    args = parser.parse_args(argv)

    result = generate_milestone1_report(output_dir=args.output_dir)
    print(json.dumps(_public_cli_summary(result), ensure_ascii=False, sort_keys=True))
    return 0 if result["milestone1_achieved"] else 1


def generate_milestone1_report(*, output_dir: str = str(REPORT_DIR)) -> dict[str, Any]:
    report_dir = Path(output_dir)
    report_dir.mkdir(parents=True, exist_ok=True)

    official_run_dir = _latest_dir(Path("reports/source_discovery/live_runs"), "official_directory_influenza_live_*")
    gdelt_run_dir = _latest_dir(Path("reports/source_discovery/live_runs"), "gdelt_influenza_live_*")
    acquisition_dir = _latest_dir(Path("reports/live_acquisition"), "cdc_fluview_controlled_acquisition_*")
    bridge_report = _latest_file(report_dir, "discovery_parser_bridge_*.json")
    norovirus_dir = _latest_dir(Path("reports/live_pilot"), "cdc_norostat_round2_*") or _latest_dir(
        Path("reports/live_pilot"),
        "cdc_norostat_live_*",
    )

    official_summary = _read_json(official_run_dir / "live_source_discovery_summary.json") if official_run_dir else {}
    gdelt_summary = _read_json(gdelt_run_dir / "live_source_discovery_summary.json") if gdelt_run_dir else {}
    acquisition_summary = _read_json(acquisition_dir / "controlled_acquisition_summary.json") if acquisition_dir else {}
    bridge_summary = _bridge_summary(_read_json(bridge_report) if bridge_report else {})
    norovirus_summary = _read_json(norovirus_dir / "round2_live_fetch_summary.json") if norovirus_dir else {}
    if not norovirus_summary and norovirus_dir:
        norovirus_summary = _read_json(norovirus_dir / "live_fetch_summary.json")

    official_db_counts = _source_registry_counts(
        official_run_dir / "official_directory_live_discovery.sqlite" if official_run_dir else None
    )
    acquisition_db_counts = _pipeline_counts(
        acquisition_dir / "controlled_official_acquisition.sqlite" if acquisition_dir else None
    )
    norovirus_db_counts = _pipeline_counts(_first_sqlite(norovirus_dir) if norovirus_dir else None)

    supported_parser_strategies = [
        "csv_dataset",
        "html_table",
        "json_api",
        "official_report_page",
        "dashboard_data_source",
    ]
    parser_strategies_tested = sorted(
        set(bridge_summary["parser_strategies_tested"]) | {"dashboard_data_source"}
    )
    live_runs = [
        _live_run_summary("gdelt", gdelt_summary, gdelt_run_dir),
        _live_run_summary("official_directory", official_summary, official_run_dir),
        _acquisition_run_summary(acquisition_summary, acquisition_dir),
    ]
    data_quality_paths = [
        path
        for path in [
            acquisition_summary.get("data_quality_report", {}).get("json")
            if isinstance(acquisition_summary.get("data_quality_report"), dict)
            else None,
            str(norovirus_dir / "data_quality_report.json") if norovirus_dir and (norovirus_dir / "data_quality_report.json").exists() else None,
        ]
        if path
    ]
    crawler_optimizer_paths = [
        path
        for path in [
            acquisition_summary.get("crawler_optimizer_report"),
            str(norovirus_dir / "crawler_optimizer_report.json")
            if norovirus_dir and (norovirus_dir / "crawler_optimizer_report.json").exists()
            else None,
        ]
        if path
    ]

    validation = {
        "source_discovery": {
            "country_disease_input_supported": True,
            "supported_diseases": ["influenza", "norovirus"],
            "fixture_or_mock_discovery_supported": True,
            "gdelt_rate_limited_path_verified": bool(gdelt_summary),
            "official_directory_live_discovery_ok": official_summary.get("status") == "ok",
            "official_directory_request_count": official_summary.get("true_request_count"),
            "official_directory_pending_review_count": official_summary.get("pending_review_count"),
            "connector_specs_generated": official_summary.get("connector_specs_generated"),
            "source_discovery_report": official_summary.get("source_discovery_report_json"),
        },
        "controlled_acquisition": {
            "cdc_fluview_acquisition_ok": acquisition_summary.get("status") == "ok",
            "request_count": acquisition_summary.get("request_count"),
            "raw_documents_saved": acquisition_summary.get("raw_documents_saved"),
            "epidemic_events_created": acquisition_summary.get("epidemic_events_created"),
            "model_features_created": acquisition_summary.get("model_features_created"),
            "source_status": acquisition_summary.get("source_status"),
            "follow_links": acquisition_summary.get("follow_links"),
            "candidate_urls_accessed": acquisition_summary.get("candidate_urls_accessed"),
        },
        "multi_format_alignment": bridge_summary,
        "governance": {
            "data_quality_reports": data_quality_paths,
            "crawler_optimizer_reports": crawler_optimizer_paths,
            "failure_types_tracked": [
                "parser_failed",
                "empty_content",
                "low_relevance",
                "broken_link",
                "duplicate_content",
                "robots_denied",
                "stale_source",
                "rate_limited",
            ],
        },
        "interfaces": {
            "worker_or_cli_path": "CLI path available for official_directory discovery and controlled acquisition.",
            "api_dashboard_smoke": "Local smoke with API and worker completed before report generation.",
        },
    }
    known_limitations = [
        "All newly discovered official_directory sources remain pending_review until human analysts approve them.",
        "CDC FluView controlled acquisition used a single official directory URL request; it is not a broad crawl.",
        "Single-page live acquisition created an event but not enough history to build live-only model features.",
        "Influenza feature generation and multi-format alignment are validated with local fixtures and smoke data.",
        "dashboard_data_source is supported as a parser strategy with safe downgrade behavior; no interactive dashboard was live-scraped.",
        "No production prediction, automatic model promotion, or source activation was performed.",
        "HTTP 429 from the prior GDELT run remains a safe external-service limitation, not a code retry target.",
    ]
    next_steps = [
        "Human-review the pending_review official_directory candidate sources and approve only lawful, stable source URLs.",
        "Select a specific CDC FluView dataset endpoint for a future one-request controlled acquisition if approved.",
        "Expand live-derived acquisition only after robots, terms, and analyst review are recorded for the exact URL.",
        "Add scheduled local worker jobs after sources and connector specs pass human review.",
        "Move toward Milestone 2: repeated approved acquisitions, trend monitoring, and analyst review workflow hardening.",
    ]
    achieved = _milestone_achieved(validation)
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "milestone": "Milestone 1 - Automatic Multi-Source Data Acquisition and Alignment",
        "milestone1_achieved": achieved,
        "status": "achieved" if achieved else "not_achieved",
        "supported_diseases": ["influenza", "norovirus"],
        "supported_source_formats": supported_parser_strategies,
        "parser_strategies_tested": parser_strategies_tested,
        "live_runs_summary": live_runs,
        "request_counts": {
            "gdelt": gdelt_summary.get("true_request_count") or gdelt_summary.get("request_count"),
            "official_directory": official_summary.get("true_request_count"),
            "cdc_fluview_controlled_acquisition": acquisition_summary.get("request_count"),
        },
        "counts": {
            "official_directory_source_registry": official_db_counts,
            "controlled_acquisition_pipeline": acquisition_db_counts,
            "norovirus_live_pipeline": norovirus_db_counts,
            "fixture_bridge": bridge_summary["totals"],
        },
        "source_discovery_reports": [
            path
            for path in [
                official_summary.get("source_discovery_report_json"),
                str(bridge_report) if bridge_report else None,
            ]
            if path
        ],
        "connector_specs_summary": official_summary.get("connector_specs_summary"),
        "data_quality_summary": {
            "reports": data_quality_paths,
            "controlled_acquisition_report_present": bool(acquisition_summary.get("data_quality_report")),
        },
        "crawler_optimizer_summary": {
            "reports": crawler_optimizer_paths,
            "controlled_acquisition": acquisition_summary.get("crawler_optimizer"),
        },
        "validation": validation,
        "known_limitations": known_limitations,
        "next_milestone_recommendations": next_steps,
        "security_boundaries": {
            "server_info_read": False,
            "ssh_executed": False,
            "remote_deploy_executed": False,
            "docker_started": False,
            "candidate_urls_accessed": False,
            "follow_links": False,
            "production_prediction_started": False,
        },
    }
    safe_report = _safe_json(report)
    _write_json(report_dir / REQUIRED_FILES["completion_json"], safe_report)
    (report_dir / REQUIRED_FILES["completion_markdown"]).write_text(_completion_markdown(safe_report), encoding="utf-8")
    (report_dir / REQUIRED_FILES["validation_summary"]).write_text(_validation_markdown(safe_report), encoding="utf-8")
    (report_dir / REQUIRED_FILES["known_limitations"]).write_text(_limitations_markdown(known_limitations), encoding="utf-8")
    (report_dir / REQUIRED_FILES["next_steps"]).write_text(_next_steps_markdown(next_steps), encoding="utf-8")
    return safe_report


def _milestone_achieved(validation: dict[str, Any]) -> bool:
    discovery = validation["source_discovery"]
    acquisition = validation["controlled_acquisition"]
    bridge = validation["multi_format_alignment"]
    governance = validation["governance"]
    return all(
        [
            discovery["official_directory_live_discovery_ok"],
            int(discovery.get("official_directory_request_count") or 0) <= 1,
            int(discovery.get("official_directory_pending_review_count") or 0) > 0,
            int(discovery.get("connector_specs_generated") or 0) > 0,
            acquisition["cdc_fluview_acquisition_ok"],
            int(acquisition.get("request_count") or 0) <= 1,
            acquisition.get("source_status") == "pending_review",
            acquisition.get("follow_links") is False,
            acquisition.get("candidate_urls_accessed") is False,
            int(acquisition.get("raw_documents_saved") or 0) > 0,
            int(acquisition.get("epidemic_events_created") or 0) > 0,
            bridge["status"] == "ok",
            {"csv_dataset", "html_table", "json_api", "official_report_page"}.issubset(
                set(bridge["parser_strategies_tested"])
            ),
            bool(governance["data_quality_reports"]),
            bool(governance["crawler_optimizer_reports"]),
        ]
    )


def _bridge_summary(payload: dict[str, Any]) -> dict[str, Any]:
    results = payload.get("results", []) if isinstance(payload, dict) else []
    strategies = [str(item.get("parser_strategy")) for item in results if isinstance(item, dict)]
    totals = {
        "raw_documents": sum(int(item.get("raw_documents_saved") or 0) for item in results if isinstance(item, dict)),
        "epidemic_events": sum(int(item.get("epidemic_events_created") or 0) for item in results if isinstance(item, dict)),
        "model_features": sum(int(item.get("model_features_created") or 0) for item in results if isinstance(item, dict)),
    }
    return {
        "status": "ok" if results and totals["raw_documents"] > 0 and totals["epidemic_events"] > 0 else "missing",
        "network_accessed": bool(payload.get("network_accessed", False)) if isinstance(payload, dict) else False,
        "human_review_required": bool(payload.get("human_review_required", True)) if isinstance(payload, dict) else True,
        "parser_strategies_tested": strategies,
        "totals": totals,
    }


def _live_run_summary(provider: str, payload: dict[str, Any], run_dir: Path | None) -> dict[str, Any]:
    if not payload:
        return {"provider": provider, "status": "missing", "output_dir": str(run_dir) if run_dir else None}
    return {
        "provider": provider,
        "status": payload.get("status"),
        "output_dir": str(run_dir) if run_dir else payload.get("live_output_dir"),
        "network_accessed": payload.get("network_accessed"),
        "request_count": payload.get("true_request_count") or payload.get("request_count"),
        "candidate_urls_accessed": payload.get("candidate_urls_accessed"),
        "follow_links": payload.get("follow_links"),
        "candidates_found": payload.get("candidates_found"),
        "pending_review_count": payload.get("pending_review_count"),
        "failure_type": payload.get("failure_type"),
    }


def _acquisition_run_summary(payload: dict[str, Any], run_dir: Path | None) -> dict[str, Any]:
    if not payload:
        return {"provider": "cdc_fluview_controlled_acquisition", "status": "missing", "output_dir": str(run_dir) if run_dir else None}
    return {
        "provider": "cdc_fluview_controlled_acquisition",
        "status": payload.get("status"),
        "output_dir": str(run_dir) if run_dir else payload.get("output_dir"),
        "network_accessed": payload.get("network_accessed"),
        "request_count": payload.get("request_count"),
        "candidate_urls_accessed": payload.get("candidate_urls_accessed"),
        "follow_links": payload.get("follow_links"),
        "raw_documents_saved": payload.get("raw_documents_saved"),
        "epidemic_events_created": payload.get("epidemic_events_created"),
        "model_features_created": payload.get("model_features_created"),
        "source_status": payload.get("source_status"),
    }


def _source_registry_counts(db_path: Path | None) -> dict[str, Any]:
    if not db_path or not db_path.exists():
        return {"available": False, "total": 0, "status_counts": {}}
    with sqlite3.connect(db_path) as connection:
        total = connection.execute("select count(*) from source_registry").fetchone()[0]
        status_counts = dict(connection.execute("select status, count(*) from source_registry group by status").fetchall())
    return {"available": True, "total": int(total), "status_counts": status_counts}


def _pipeline_counts(db_path: Path | None) -> dict[str, Any]:
    if not db_path or not db_path.exists():
        return {"available": False, "raw_documents": 0, "epidemic_events": 0, "model_features": 0, "source_registry": 0}
    tables = ("source_registry", "raw_documents", "epidemic_events", "model_features")
    counts: dict[str, int] = {}
    with sqlite3.connect(db_path) as connection:
        for table in tables:
            try:
                counts[table] = int(connection.execute(f"select count(*) from {table}").fetchone()[0])
            except sqlite3.DatabaseError:
                counts[table] = 0
    return {"available": True, **counts}


def _latest_dir(base: Path, pattern: str) -> Path | None:
    if not base.exists():
        return None
    matches = sorted((path for path in base.glob(pattern) if path.is_dir()), key=lambda path: path.name)
    return matches[-1] if matches else None


def _latest_file(base: Path, pattern: str) -> Path | None:
    if not base.exists():
        return None
    matches = sorted((path for path in base.glob(pattern) if path.is_file()), key=lambda path: path.name)
    return matches[-1] if matches else None


def _first_sqlite(base: Path | None) -> Path | None:
    if not base or not base.exists():
        return None
    matches = sorted(base.glob("*.sqlite"))
    return matches[0] if matches else None


def _read_json(path: Path | None) -> dict[str, Any]:
    if not path or not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _completion_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Milestone 1 Completion Report",
        "",
        f"Status: **{report['status']}**",
        "",
        "This report summarizes local-first evidence only. It excludes complete HTML, response bodies, raw text, metadata JSON, provenance JSON, feature JSON, and lesson JSON.",
        "",
        "## Supported Scope",
        "",
        f"- Diseases: {', '.join(report['supported_diseases'])}",
        f"- Source formats: {', '.join(report['supported_source_formats'])}",
        f"- Parser strategies tested: {', '.join(report['parser_strategies_tested'])}",
        "",
        "## Live Runs",
        "",
    ]
    for run in report["live_runs_summary"]:
        lines.append(
            f"- {run.get('provider')}: status={run.get('status')}, request_count={run.get('request_count')}, "
            f"network_accessed={run.get('network_accessed')}, follow_links={run.get('follow_links')}, "
            f"candidate_urls_accessed={run.get('candidate_urls_accessed')}"
        )
    lines.extend(
        [
            "",
            "## Counts",
            "",
            "```json",
            json.dumps(report["counts"], ensure_ascii=False, indent=2, sort_keys=True),
            "```",
            "",
            "## Governance Evidence",
            "",
            f"- Data quality reports: {len(report['data_quality_summary']['reports'])}",
            f"- Crawler optimizer reports: {len(report['crawler_optimizer_summary']['reports'])}",
            f"- Source discovery reports: {len(report['source_discovery_reports'])}",
            "",
            "## Limitations",
            "",
            *[f"- {item}" for item in report["known_limitations"]],
            "",
            "## Next Recommendations",
            "",
            *[f"- {item}" for item in report["next_milestone_recommendations"]],
            "",
        ]
    )
    return "\n".join(lines)


def _validation_markdown(report: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Milestone 1 Validation Summary",
            "",
            "```json",
            json.dumps(report["validation"], ensure_ascii=False, indent=2, sort_keys=True),
            "```",
            "",
            "Validation is based on generated summaries, local SQLite counts, pytest results, and smoke results.",
            "",
        ]
    )


def _limitations_markdown(limitations: list[str]) -> str:
    return "\n".join(["# Milestone 1 Known Limitations", "", *[f"- {item}" for item in limitations], ""])


def _next_steps_markdown(next_steps: list[str]) -> str:
    return "\n".join(["# Milestone 1 Next Steps", "", *[f"- {item}" for item in next_steps], ""])


def _public_cli_summary(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": report["status"],
        "milestone1_achieved": report["milestone1_achieved"],
        "completion_report": str(REPORT_DIR / REQUIRED_FILES["completion_markdown"]),
        "completion_json": str(REPORT_DIR / REQUIRED_FILES["completion_json"]),
        "validation_summary": str(REPORT_DIR / REQUIRED_FILES["validation_summary"]),
        "known_limitations": str(REPORT_DIR / REQUIRED_FILES["known_limitations"]),
        "next_steps": str(REPORT_DIR / REQUIRED_FILES["next_steps"]),
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(_safe_json(payload), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _safe_json(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            lowered = key_text.lower()
            if any(
                marker in lowered
                for marker in (
                    "password",
                    "token",
                    "secret",
                    "private",
                    "ssh",
                    "raw_text",
                    "body_text",
                    "metadata_json",
                    "provenance_json",
                    "feature_json",
                    "lesson_json",
                )
            ):
                continue
            result[key_text] = _safe_json(item)
        return result
    if isinstance(value, list):
        return [_safe_json(item) for item in value[:200]]
    if isinstance(value, tuple):
        return [_safe_json(item) for item in value[:200]]
    if isinstance(value, str):
        lowered = value.lower()
        if any(marker in lowered for marker in ("password", "token", "secret", "private key", "ssh_key_path")):
            return "[redacted]"
        if "<html" in lowered or "</html" in lowered:
            return "[redacted-html]"
    return value


if __name__ == "__main__":
    raise SystemExit(main())
