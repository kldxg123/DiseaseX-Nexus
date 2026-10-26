from __future__ import annotations

import argparse
import json
import shutil
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select

from cleaner_agent.agent import CleanerAgent
from cleaner_agent.features import FeatureBuilder
from common.db import get_session
from common.models import EpidemicEvent, HumanFeedback, ModelExperienceBank, ModelFeature, Prediction, RawDocument
from crawler_optimizer_agent import CrawlerOptimizerAgent
from crawler_optimizer_agent.reporting import summarize_optimizer_report
from data_quality_agent.report import build_data_quality_report, render_markdown_report

DEFAULT_PILOT_DIR = Path("reports/live_pilot/cdc_norostat_live_20260702T115524Z")
DEFAULT_SOURCE_DB_NAME = "cdc_norostat_live_pilot.sqlite"
REPROCESS_DB_NAME = "cdc_norostat_reprocess.sqlite"


def reprocess_live_pilot(
    *,
    pilot_dir: str | Path | None = None,
    source_db: str | Path | None = None,
    output_dir: str | Path = "reports/live_pilot",
    run_timestamp: str | None = None,
) -> dict[str, Any]:
    source_db_path = _resolve_source_db(pilot_dir=pilot_dir, source_db=source_db)
    source_pilot_dir = Path(pilot_dir) if pilot_dir else source_db_path.parent
    timestamp = run_timestamp or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    reprocess_dir = _unique_reprocess_dir(Path(output_dir), timestamp)
    reprocess_dir.mkdir(parents=True, exist_ok=False)
    reprocess_db = reprocess_dir / REPROCESS_DB_NAME
    shutil.copy2(source_db_path, reprocess_db)

    warnings: list[str] = []
    original_summary = _read_original_summary(source_pilot_dir, warnings)
    database_url = _sqlite_url(reprocess_db)

    with get_session(database_url) as session:
        before = _database_counts(session)
        _clear_derived_tables(session)
        _reset_raw_documents(session)
        after_cleanup = _database_counts(session)
        cleaner_summary = CleanerAgent(session).process_pending(
            limit=max(1000, int(after_cleanup["raw_documents_count"]) + 10),
            country="United States",
            disease="norovirus",
        )
        feature_summary = FeatureBuilder(session).build(
            country="United States",
            disease="norovirus",
            horizons_days=(7, 28),
            split_type="train",
            replace_existing=True,
        )
        feature_counts = _feature_counts_by_horizon(session)
        if feature_counts.get("7", 0) == 0:
            warnings.append("horizon=7 features were not generated; review event week coverage.")
        if feature_counts.get("28", 0) == 0:
            warnings.append("horizon=28 features were not generated; the local history window may be too short.")
        data_quality_report = build_data_quality_report(session, country="United States", disease="norovirus")
        optimizer_report = summarize_optimizer_report(
            CrawlerOptimizerAgent(session).analyze(country="United States", disease="norovirus"),
            max_failure_cases=20,
            max_actions=20,
            max_connector_specs=10,
        )
        after_reprocess = _database_counts(session)

    comparison = _build_comparison(
        original=original_summary,
        before=before,
        after=after_reprocess,
        cleaner_summary=cleaner_summary,
        feature_counts=feature_counts,
        optimizer_report=optimizer_report,
        warnings=warnings,
    )
    output_files = _write_outputs(
        reprocess_dir=reprocess_dir,
        reprocess_db=reprocess_db,
        source_db=source_db_path,
        original_summary=original_summary,
        before=before,
        after_cleanup=after_cleanup,
        after_reprocess=after_reprocess,
        cleaner_summary=cleaner_summary,
        feature_summary=feature_summary,
        feature_counts=feature_counts,
        data_quality_report=data_quality_report,
        optimizer_report=optimizer_report,
        comparison=comparison,
        warnings=warnings,
    )
    return {
        "status": "ok",
        "reprocess_dir": str(reprocess_dir.as_posix()),
        "reprocess_db": str(reprocess_db.as_posix()),
        "source_db_unchanged": True,
        "network_access": False,
        "cdc_requested": False,
        "docker_started": False,
        "ssh_or_remote_deploy": False,
        "orm_or_alembic_changed": False,
        "original_feature_rows": original_summary.get("feature_rows"),
        "feature_rows_total": after_reprocess["model_features_count"],
        "feature_rows_h7": feature_counts.get("7", 0),
        "feature_rows_h28": feature_counts.get("28", 0),
        "original_optimizer_failure_case_count": original_summary.get("crawler_optimizer_failure_case_count"),
        "crawler_optimizer_failure_case_count": optimizer_report.get("failure_case_count", 0),
        "warnings": warnings,
        "output_files": output_files,
    }


def _resolve_source_db(*, pilot_dir: str | Path | None, source_db: str | Path | None) -> Path:
    if source_db:
        path = Path(source_db)
    else:
        base = Path(pilot_dir) if pilot_dir else DEFAULT_PILOT_DIR
        path = base / DEFAULT_SOURCE_DB_NAME
    if not path.exists():
        raise FileNotFoundError(f"Pilot database not found: {path}")
    return path


def _unique_reprocess_dir(output_dir: Path, timestamp: str) -> Path:
    base = output_dir / f"cdc_norostat_reprocess_{timestamp}"
    if not base.exists():
        return base
    for index in range(1, 100):
        candidate = output_dir / f"cdc_norostat_reprocess_{timestamp}_{index:02d}"
        if not candidate.exists():
            return candidate
    raise FileExistsError("Could not allocate a unique reprocess output directory.")


def _sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.resolve().as_posix()}"


def _database_counts(session) -> dict[str, Any]:
    events = list(session.scalars(select(EpidemicEvent.iso_week)).all())
    return {
        "raw_documents_count": session.query(RawDocument).count(),
        "epidemic_events_count": session.query(EpidemicEvent).count(),
        "model_features_count": session.query(ModelFeature).count(),
        "predictions_count": session.query(Prediction).count(),
        "feedback_count": session.query(HumanFeedback).count(),
        "experience_count": session.query(ModelExperienceBank).count(),
        "iso_week_unique_count": len({str(value) for value in events if value}),
    }


def _clear_derived_tables(session) -> None:
    for model in (ModelExperienceBank, HumanFeedback, Prediction, ModelFeature, EpidemicEvent):
        session.execute(delete(model))
    session.flush()


def _reset_raw_documents(session) -> None:
    for document in session.scalars(select(RawDocument)).all():
        document.extraction_status = "pending"
    session.flush()


def _feature_counts_by_horizon(session) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for horizon in session.scalars(select(ModelFeature.horizon_days)).all():
        counts[str(int(horizon))] += 1
    return dict(sorted(counts.items(), key=lambda item: int(item[0])))


def _read_original_summary(pilot_dir: Path, warnings: list[str]) -> dict[str, Any]:
    live_fetch = _read_json(pilot_dir / "live_fetch_summary.json", warnings)
    postmortem = _read_json(pilot_dir / "postmortem_report.json", warnings)
    data_quality = _read_json(pilot_dir / "data_quality_report.json", warnings)
    optimizer = _read_json(pilot_dir / "crawler_optimizer_report.json", warnings)
    feature_rows = _nested_int(live_fetch, ("features", "feature_rows"))
    if feature_rows is None:
        feature_rows = _nested_int(postmortem, ("model_features_summary", "total"))
    events_created = _nested_int(live_fetch, ("cleaner", "events_created"))
    if events_created is None:
        events_created = _nested_int(postmortem, ("epidemic_events_summary", "total"))
    return {
        "raw_documents_saved": _int_or_none(live_fetch.get("raw_documents_saved")),
        "events_created": events_created,
        "feature_rows": feature_rows,
        "crawler_optimizer_failure_case_count": _int_or_none(optimizer.get("failure_case_count")),
        "data_quality_warning_count": len(data_quality.get("warnings", []) or []) if isinstance(data_quality, dict) else None,
    }


def _read_json(path: Path, warnings: list[str]) -> dict[str, Any]:
    if not path.exists():
        warnings.append(f"Original report missing: {path.name}")
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        warnings.append(f"Original report is not valid JSON: {path.name}")
        return {}


def _nested_int(payload: dict[str, Any], keys: tuple[str, ...]) -> int | None:
    value: Any = payload
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return _int_or_none(value)


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _build_comparison(
    *,
    original: dict[str, Any],
    before: dict[str, Any],
    after: dict[str, Any],
    cleaner_summary: dict[str, Any],
    feature_counts: dict[str, int],
    optimizer_report: dict[str, Any],
    warnings: list[str],
) -> dict[str, Any]:
    original_optimizer = original.get("crawler_optimizer_failure_case_count")
    reprocessed_optimizer = int(optimizer_report.get("failure_case_count", 0))
    original_features = original.get("feature_rows")
    return {
        "original": {
            "raw_documents_saved": original.get("raw_documents_saved") or before.get("raw_documents_count"),
            "events_created": original.get("events_created") or before.get("epidemic_events_count"),
            "feature_rows": original_features if original_features is not None else before.get("model_features_count"),
            "crawler_optimizer_failure_case_count": original_optimizer,
        },
        "reprocessed": {
            "raw_documents_count": after["raw_documents_count"],
            "events_created": int(cleaner_summary.get("events_created", 0)),
            "feature_rows_total": after["model_features_count"],
            "feature_rows_h7": feature_counts.get("7", 0),
            "feature_rows_h28": feature_counts.get("28", 0),
            "crawler_optimizer_failure_case_count": reprocessed_optimizer,
        },
        "improvements": {
            "features_generated": after["model_features_count"] > int(original_features or 0),
            "optimizer_failures_reduced": (
                original_optimizer is not None and reprocessed_optimizer < int(original_optimizer)
            ),
            "iso_weeks_recovered": after["iso_week_unique_count"] > before["iso_week_unique_count"],
        },
        "warnings": warnings,
    }


def _write_outputs(
    *,
    reprocess_dir: Path,
    reprocess_db: Path,
    source_db: Path,
    original_summary: dict[str, Any],
    before: dict[str, Any],
    after_cleanup: dict[str, Any],
    after_reprocess: dict[str, Any],
    cleaner_summary: dict[str, Any],
    feature_summary: dict[str, Any],
    feature_counts: dict[str, int],
    data_quality_report: dict[str, Any],
    optimizer_report: dict[str, Any],
    comparison: dict[str, Any],
    warnings: list[str],
) -> dict[str, str]:
    summary = {
        "status": "ok",
        "generated_at": datetime.now(UTC).isoformat(),
        "source_db_name": source_db.name,
        "reprocess_db_name": reprocess_db.name,
        "source_db_unchanged": True,
        "before_reprocess": before,
        "after_cleanup": after_cleanup,
        "after_reprocess": after_reprocess,
        "cleaner": cleaner_summary,
        "features": {
            **feature_summary,
            "horizon_counts": feature_counts,
            "warnings": [warning for warning in warnings if "horizon" in warning],
        },
        "data_quality": data_quality_report.get("summary", {}),
        "crawler_optimizer": {
            "failure_case_count": optimizer_report.get("failure_case_count", 0),
            "top_failure_types": optimizer_report.get("top_failure_types", []),
            "recommended_action_count": optimizer_report.get("recommended_action_count", 0),
        },
        "comparison": comparison,
        "warnings": warnings,
        "safety_notes": [
            "No network access was performed during local reprocess.",
            "CDC was not requested again.",
            "The original pilot database and reports were not overwritten.",
            "Production prediction was not run.",
            "Outputs are safe summaries and require human review.",
        ],
    }
    paths = {
        "reprocess_summary_json": reprocess_dir / "reprocess_summary.json",
        "reprocess_summary_md": reprocess_dir / "reprocess_summary.md",
        "data_quality_report_json": reprocess_dir / "data_quality_report.json",
        "data_quality_report_md": reprocess_dir / "data_quality_report.md",
        "crawler_optimizer_report_json": reprocess_dir / "crawler_optimizer_report.json",
        "comparison_with_original_json": reprocess_dir / "comparison_with_original.json",
        "rollback_commands_md": reprocess_dir / "rollback_commands.md",
    }
    _write_json(paths["reprocess_summary_json"], summary)
    paths["reprocess_summary_md"].write_text(_render_summary_markdown(summary, original_summary), encoding="utf-8")
    _write_json(paths["data_quality_report_json"], data_quality_report)
    paths["data_quality_report_md"].write_text(render_markdown_report(data_quality_report), encoding="utf-8")
    _write_json(paths["crawler_optimizer_report_json"], optimizer_report)
    _write_json(paths["comparison_with_original_json"], comparison)
    paths["rollback_commands_md"].write_text(_render_rollback(reprocess_dir), encoding="utf-8")
    return {key: str(path.as_posix()) for key, path in paths.items()}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _render_summary_markdown(summary: dict[str, Any], original_summary: dict[str, Any]) -> str:
    comparison = summary["comparison"]
    lines = [
        "# CDC NoroSTAT Live Pilot Local Reprocess",
        "",
        "## Executive Summary",
        "",
        _json_block(
            {
                "status": summary["status"],
                "source_db_unchanged": summary["source_db_unchanged"],
                "feature_rows_total": summary["after_reprocess"]["model_features_count"],
                "feature_rows_h7": summary["features"]["horizon_counts"].get("7", 0),
                "feature_rows_h28": summary["features"]["horizon_counts"].get("28", 0),
                "optimizer_failure_case_count": summary["crawler_optimizer"]["failure_case_count"],
            }
        ),
        "",
        "## Original Pilot Result",
        "",
        _json_block(original_summary),
        "",
        "## Reprocess Method",
        "",
        "- Copied the original pilot database into a new local reprocess directory.",
        "- Cleared derived tables only in the copied database.",
        "- Rebuilt events from existing raw documents with the repaired cleaner.",
        "- Built model features for 7-day and 28-day horizons without running production prediction.",
        "",
        "## Cleaner Reprocess Result",
        "",
        _json_block(summary["cleaner"]),
        "",
        "## FeatureBuilder Result",
        "",
        _json_block(summary["features"]),
        "",
        "## ISO Week Recovery",
        "",
        _json_block(
            {
                "before_unique_iso_weeks": summary["before_reprocess"]["iso_week_unique_count"],
                "after_unique_iso_weeks": summary["after_reprocess"]["iso_week_unique_count"],
                "iso_weeks_recovered": comparison["improvements"]["iso_weeks_recovered"],
            }
        ),
        "",
        "## Crawler Optimizer Result",
        "",
        _json_block(summary["crawler_optimizer"]),
        "",
        "## Remaining Issues",
        "",
        _markdown_list(summary["warnings"]),
        "",
        "## Recommended Next Step",
        "",
        "Have an analyst review the recovered weekly events and feature labels before any broader live-data access or forecasting run.",
        "",
        "## Safety Notes",
        "",
        "- No network access was performed.",
        "- CDC was not requested again.",
        "- The original pilot database and reports were not overwritten.",
        "- Production prediction was not run.",
        "- Results remain auxiliary analysis and require human review.",
        "",
    ]
    return "\n".join(lines)


def _render_rollback(reprocess_dir: Path) -> str:
    return "\n".join(
        [
            "# Rollback Commands",
            "",
            "This reprocess output is isolated. To remove it after review, delete this directory:",
            "",
            f"`{reprocess_dir.as_posix()}`",
            "",
            "PowerShell example:",
            "",
            f"`Remove-Item -Recurse -LiteralPath \"{reprocess_dir.as_posix()}\"`",
            "",
            "Before deleting, confirm:",
            "",
            "- The original pilot database still exists.",
            "- The original live fetch reports were not overwritten.",
            "- No production database was written.",
            "- No full HTML, raw text, or sensitive configuration was saved in the summary outputs.",
            "",
        ]
    )


def _json_block(value: Any) -> str:
    return "```json\n" + json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n```"


def _markdown_list(values: list[Any]) -> str:
    if not values:
        return "- none"
    return "\n".join(f"- {value}" for value in values)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Copy and locally reprocess the CDC NoroSTAT live pilot database.")
    parser.add_argument("--pilot-dir", default=None)
    parser.add_argument("--source-db", default=None)
    parser.add_argument("--output-dir", default="reports/live_pilot")
    args = parser.parse_args(argv)
    result = reprocess_live_pilot(
        pilot_dir=args.pilot_dir,
        source_db=args.source_db,
        output_dir=args.output_dir,
    )
    print(
        json.dumps(
            {
                "status": result["status"],
                "reprocess_dir": result["reprocess_dir"],
                "reprocess_db": result["reprocess_db"],
                "source_db_unchanged": result["source_db_unchanged"],
                "network_access": result["network_access"],
                "cdc_requested": result["cdc_requested"],
                "feature_rows_total": result["feature_rows_total"],
                "feature_rows_h7": result["feature_rows_h7"],
                "feature_rows_h28": result["feature_rows_h28"],
                "crawler_optimizer_failure_case_count": result["crawler_optimizer_failure_case_count"],
                "warnings": result["warnings"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
