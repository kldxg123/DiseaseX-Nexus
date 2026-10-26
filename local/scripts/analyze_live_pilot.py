from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from sqlalchemy import select

from cleaner_agent.event_extraction import extract_event_from_raw_document
from cleaner_agent.feature_engineering import aggregate_events_weekly, build_features_from_weekly_aggregates
from common.db import get_session
from common.models import EpidemicEvent, ModelFeature, RawDocument

DEFAULT_PILOT_DIR = Path("reports/live_pilot/cdc_norostat_live_20260702T115524Z")


def analyze_live_pilot(
    *,
    pilot_dir: str | Path | None = None,
    pilot_db: str | Path | None = None,
    write_reports: bool = True,
) -> dict[str, Any]:
    resolved_dir = Path(pilot_dir) if pilot_dir else None
    resolved_db = Path(pilot_db) if pilot_db else _find_pilot_db(resolved_dir or DEFAULT_PILOT_DIR)
    if resolved_dir is None:
        resolved_dir = resolved_db.parent
    database_url = _sqlite_url(resolved_db)

    input_reports = _read_known_reports(resolved_dir)
    with get_session(database_url) as session:
        raw_documents = list(session.scalars(select(RawDocument).order_by(RawDocument.id)).all())
        events = list(session.scalars(select(EpidemicEvent).order_by(EpidemicEvent.id)).all())
        features = list(session.scalars(select(ModelFeature).order_by(ModelFeature.id)).all())
        raw_summary = _raw_documents_summary(raw_documents)
        event_summary = _event_summary(events)
        feature_summary = _feature_summary(features)
        repair_simulation = _simulate_feature_repair(raw_documents)

    optimizer_summary = _optimizer_summary(input_reports.get("crawler_optimizer_report"))
    feature_diagnosis = _diagnose_zero_features(
        event_summary=event_summary,
        feature_summary=feature_summary,
        repair_simulation=repair_simulation,
    )
    report = {
        "status": "ok",
        "pilot_dir": str(resolved_dir.as_posix()),
        "pilot_db": resolved_db.name,
        "executive_summary": _executive_summary(input_reports, raw_summary, event_summary, feature_summary),
        "live_fetch_result": _live_fetch_summary(input_reports.get("live_fetch_summary")),
        "raw_documents_summary": raw_summary,
        "epidemic_events_summary": event_summary,
        "model_features_summary": feature_summary,
        "why_feature_rows_were_zero": feature_diagnosis,
        "repair_simulation": repair_simulation,
        "crawler_optimizer_failure_analysis": optimizer_summary,
        "fixes_applied": [
            "CDC NoroSTAT rows are mapped as official structured dataset records.",
            "Cleaner prefers structured event metadata before rule-based body-text extraction.",
            "Crawler optimizer avoids low-relevance/empty-content false positives for structured official dataset rows.",
            "Postmortem script writes safe local JSON and Markdown reports without re-fetching external data.",
        ],
        "remaining_issues": _remaining_issues(event_summary, repair_simulation),
        "recommended_next_step": (
            "Run the local postmortem and repaired parser against fixture or approved pilot data, "
            "then have an analyst review structured field coverage before any broader live access."
        ),
        "safety_notes": [
            "No network request is performed by this analysis.",
            "No SSH, Docker, remote deployment, or external data source access is required.",
            "Reports omit full HTML, raw text, feature payloads, source provenance payloads, lesson payloads, and credentials.",
            "Any model features or predictions remain decision-support material and require human review.",
        ],
    }
    if write_reports:
        _write_reports(resolved_dir, report)
    return report


def _find_pilot_db(pilot_dir: Path) -> Path:
    candidates = sorted(pilot_dir.glob("*.sqlite"))
    if not candidates:
        raise FileNotFoundError(f"No SQLite pilot database found under {pilot_dir}")
    return candidates[0]


def _sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.resolve().as_posix()}"


def _read_known_reports(pilot_dir: Path) -> dict[str, dict[str, Any]]:
    reports: dict[str, dict[str, Any]] = {}
    for name in ("live_fetch_summary", "data_quality_report", "crawler_optimizer_report"):
        path = pilot_dir / f"{name}.json"
        if not path.exists():
            reports[name] = {}
            continue
        try:
            reports[name] = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            reports[name] = {"status": "invalid_json"}
    return reports


def _live_fetch_summary(report: dict[str, Any] | None) -> dict[str, Any]:
    report = report or {}
    connector = _safe_mapping(report.get("connector") or {})
    return {
        "status": report.get("status"),
        "connector_documents_parsed": _int(report.get("connector_documents_parsed")),
        "raw_documents_saved": _int(report.get("raw_documents_saved")),
        "duplicates": _int(report.get("duplicates")),
        "http_status_code": connector.get("status_code"),
        "request_count": report.get("request_count", 1),
        "network_reused": False,
    }


def _raw_documents_summary(raw_documents: list[RawDocument]) -> dict[str, Any]:
    statuses = Counter(str(row.extraction_status or "unknown") for row in raw_documents)
    connectors = Counter(_metadata_value(row, "connector") or "unknown" for row in raw_documents)
    source_keys = Counter(_metadata_value(row, "source_key") or "unknown" for row in raw_documents)
    return {
        "total": len(raw_documents),
        "extraction_status_counts": dict(sorted(statuses.items())),
        "connector_counts": dict(sorted(connectors.items())),
        "source_key_counts": dict(sorted(source_keys.items())),
        "country_detected_count": sum(1 for row in raw_documents if row.country_detected),
        "disease_detected_count": sum(1 for row in raw_documents if row.disease_detected),
        "structured_row_count": sum(1 for row in raw_documents if isinstance(_metadata_value(row, "row_fields"), dict)),
        "short_text_count": sum(1 for row in raw_documents if len(str(row.body_text or "").strip()) < 80),
    }


def _event_summary(events: list[EpidemicEvent]) -> dict[str, Any]:
    iso_weeks = [str(row.iso_week) for row in events if row.iso_week]
    dates = [row.event_date for row in events if row.event_date]
    countries = Counter(str(row.country or "unknown") for row in events)
    diseases = Counter(str(row.disease or "unknown") for row in events)
    extraction_methods = Counter(str(row.extraction_method or "unknown") for row in events)
    return {
        "total": len(events),
        "countries": dict(sorted(countries.items())),
        "diseases": dict(sorted(diseases.items())),
        "extraction_methods": dict(sorted(extraction_methods.items())),
        "unique_iso_week_count": len(set(iso_weeks)),
        "iso_week_min": min(iso_weeks) if iso_weeks else None,
        "iso_week_max": max(iso_weeks) if iso_weeks else None,
        "event_date_min": min(dates).isoformat() if dates else None,
        "event_date_max": max(dates).isoformat() if dates else None,
        "missing_country_count": sum(1 for row in events if not row.country),
        "missing_disease_count": sum(1 for row in events if not row.disease),
        "missing_event_date_count": sum(1 for row in events if row.event_date is None),
        "missing_iso_week_count": sum(1 for row in events if not row.iso_week),
        "missing_cases_count": sum(1 for row in events if row.cases is None),
        "missing_outbreaks_count": sum(1 for row in events if row.outbreaks_count is None),
        "rows_with_outbreaks_count": sum(1 for row in events if row.outbreaks_count is not None),
    }


def _feature_summary(features: list[ModelFeature]) -> dict[str, Any]:
    horizons = Counter(str(row.horizon_days) for row in features)
    return {
        "total": len(features),
        "horizon_counts": dict(sorted(horizons.items())),
        "target_rows": sum(1 for row in features if row.target_value is not None),
        "outbreak_label_rows": sum(1 for row in features if row.target_outbreak_label is not None),
    }


def _simulate_feature_repair(raw_documents: list[RawDocument]) -> dict[str, Any]:
    events = []
    for document in raw_documents:
        event = extract_event_from_raw_document(
            document,
            target_country="United States",
            target_disease="norovirus",
        )
        if event is not None:
            events.append(event)
    try:
        weekly = aggregate_events_weekly(events, fill_missing_weeks=True)
        rows = build_features_from_weekly_aggregates(
            weekly,
            horizons_days=(7,),
            outbreak_threshold=3,
        )
    except Exception as exc:  # pragma: no cover - defensive report path
        return {
            "status": "error",
            "event_count": len(events),
            "weekly_rows": 0,
            "feature_rows_horizon_7": 0,
            "warning": str(exc.__class__.__name__),
        }
    iso_weeks = {event.iso_week for event in events if event.iso_week}
    return {
        "status": "ok",
        "event_count": len(events),
        "unique_iso_week_count": len(iso_weeks),
        "weekly_rows": len(weekly),
        "feature_rows_horizon_7": len(rows),
        "rows_with_outbreaks_count": sum(1 for event in events if event.outbreaks_count is not None),
        "missing_outbreaks_count": sum(1 for event in events if event.outbreaks_count is None),
    }


def _diagnose_zero_features(
    *,
    event_summary: dict[str, Any],
    feature_summary: dict[str, Any],
    repair_simulation: dict[str, Any],
) -> dict[str, Any]:
    reasons: list[str] = []
    if feature_summary.get("total", 0) != 0:
        return {"feature_rows_zero": False, "reasons": [], "repair_can_generate_horizon_7": None}
    if event_summary.get("unique_iso_week_count", 0) <= 1:
        reasons.append("Existing epidemic_events collapsed into one ISO week, so no future horizon window was available.")
    if event_summary.get("missing_outbreaks_count", 0) == event_summary.get("total", -1):
        reasons.append("Existing epidemic_events did not map CDC outbreak counts.")
    if event_summary.get("missing_cases_count", 0) == event_summary.get("total", -1):
        reasons.append("CDC NoroSTAT is outbreak-oriented; case counts are absent and must not be fabricated.")
    if repair_simulation.get("unique_iso_week_count", 0) > event_summary.get("unique_iso_week_count", 0):
        reasons.append("Structured CDC row week labels can be recovered from raw document metadata.")
    return {
        "feature_rows_zero": True,
        "reasons": reasons,
        "repair_can_generate_horizon_7": repair_simulation.get("feature_rows_horizon_7", 0) > 0,
        "repair_feature_rows_horizon_7": repair_simulation.get("feature_rows_horizon_7", 0),
    }


def _optimizer_summary(report: dict[str, Any] | None) -> dict[str, Any]:
    report = report or {}
    failures = dict(report.get("summary", {}).get("failures", {}) or {})
    for item in report.get("top_failure_types", []) or []:
        if isinstance(item, dict) and item.get("failure_type"):
            failures[str(item["failure_type"])] = _int(item.get("count"))
    if not failures:
        failures = dict(Counter(str(item.get("failure_type", "unknown")) for item in report.get("failure_cases", []) if isinstance(item, dict)))
    low_relevance_count = _int(failures.get("low_relevance"))
    return {
        "status": report.get("status"),
        "failure_case_count": _int(report.get("failure_case_count") or len(report.get("failure_cases", []) or [])),
        "recommended_action_count": _int(
            report.get("recommended_action_count") or len(report.get("recommended_actions", []) or [])
        ),
        "failure_type_counts": _safe_mapping(failures),
        "low_relevance_count": low_relevance_count,
        "diagnosis": (
            "Low-relevance cases were caused by body-text keyword checks ignoring structured official dataset metadata."
            if low_relevance_count
            else "No dominant low-relevance false-positive pattern detected."
        ),
    }


def _executive_summary(
    input_reports: dict[str, dict[str, Any]],
    raw_summary: dict[str, Any],
    event_summary: dict[str, Any],
    feature_summary: dict[str, Any],
) -> dict[str, Any]:
    live = _live_fetch_summary(input_reports.get("live_fetch_summary"))
    return {
        "live_fetch_status": live.get("status"),
        "http_status_code": live.get("http_status_code"),
        "raw_documents": raw_summary["total"],
        "epidemic_events": event_summary["total"],
        "model_features": feature_summary["total"],
        "requires_human_review": True,
        "accuracy_guarantee": False,
    }


def _remaining_issues(event_summary: dict[str, Any], repair_simulation: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if repair_simulation.get("missing_outbreaks_count", 0):
        issues.append("Some CDC rows still lack outbreak count fields; downstream analysis should treat them as incomplete.")
    if event_summary.get("missing_cases_count", 0):
        issues.append("Case counts are not present in this dataset and should not be inferred from outbreak rows.")
    if repair_simulation.get("feature_rows_horizon_7", 0) == 0:
        issues.append("Feature rows remain unavailable because the local data does not cover a usable future horizon.")
    return issues


def _write_reports(pilot_dir: Path, report: dict[str, Any]) -> None:
    pilot_dir.mkdir(parents=True, exist_ok=True)
    (pilot_dir / "postmortem_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (pilot_dir / "postmortem_report.md").write_text(_render_markdown(report), encoding="utf-8")


def _render_markdown(report: dict[str, Any]) -> str:
    sections = [
        "# CDC NoroSTAT Live Pilot Postmortem",
        "## Executive Summary",
        _json_block(report["executive_summary"]),
        "## Live Fetch Result",
        _json_block(report["live_fetch_result"]),
        "## Raw Documents Summary",
        _json_block(report["raw_documents_summary"]),
        "## Epidemic Events Summary",
        _json_block(report["epidemic_events_summary"]),
        "## Why Feature Rows Were Zero",
        _json_block(report["why_feature_rows_were_zero"]),
        "## Crawler Optimizer Failure Analysis",
        _json_block(report["crawler_optimizer_failure_analysis"]),
        "## Fixes Applied",
        "\n".join(f"- {item}" for item in report["fixes_applied"]),
        "## Remaining Issues",
        "\n".join(f"- {item}" for item in report["remaining_issues"]) or "- None",
        "## Recommended Next Step",
        report["recommended_next_step"],
        "## Safety Notes",
        "\n".join(f"- {item}" for item in report["safety_notes"]),
    ]
    return "\n\n".join(sections) + "\n"


def _json_block(value: dict[str, Any]) -> str:
    return "```json\n" + json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n```"


def _safe_mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    blocked = {"html", "raw_text", "body_text", "feature_json", "lesson_json", "provenance_json", "metadata_json"}
    return {
        str(key): item
        for key, item in value.items()
        if str(key).lower() not in blocked and not any(token in str(key).lower() for token in ("password", "token", "secret", "key"))
    }


def _metadata_value(row: RawDocument, key: str) -> Any:
    metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
    return metadata.get(key)


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Analyze local CDC NoroSTAT live pilot outputs without network access.")
    parser.add_argument("--pilot-dir", default=None)
    parser.add_argument("--pilot-db", default=None)
    args = parser.parse_args(argv)
    report = analyze_live_pilot(pilot_dir=args.pilot_dir, pilot_db=args.pilot_db, write_reports=True)
    print(
        json.dumps(
            {
                "status": report["status"],
                "pilot_dir": report["pilot_dir"],
                "postmortem_report_json": "postmortem_report.json",
                "postmortem_report_md": "postmortem_report.md",
                "feature_rows_zero": report["why_feature_rows_were_zero"]["feature_rows_zero"],
                "repair_feature_rows_horizon_7": report["why_feature_rows_were_zero"]["repair_feature_rows_horizon_7"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
