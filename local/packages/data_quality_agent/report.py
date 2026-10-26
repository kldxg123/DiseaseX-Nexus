from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from data_quality_agent.label_audit import LabelAuditResult, audit_model_feature_labels
from data_quality_agent.profile import DataQualityProfile, TableQualitySummary, build_data_quality_profile

OMITTED_FIELDS = {
    "raw_text",
    "body_text",
    "html",
    "feature_json",
    "lesson_json",
    "provenance_json",
    "metadata_json",
    "experience_json",
}


def data_quality_profile_to_dict(profile: DataQualityProfile) -> dict:
    return _safe_jsonable(profile)


def label_audit_to_dict(audit: LabelAuditResult) -> dict:
    return _safe_jsonable(audit)


def build_data_quality_report(
    session: Session,
    *,
    country: str | None = None,
    disease: str | None = None,
) -> dict:
    profile = build_data_quality_profile(session)
    audit = audit_model_feature_labels(session, country=country, disease=disease)
    profile_payload = data_quality_profile_to_dict(profile)
    audit_payload = label_audit_to_dict(audit)
    warnings = list(profile_payload.get("overall_warnings", []))
    warnings.extend(audit_payload.get("leakage_warnings", []))
    warnings.extend(audit_payload.get("missing_target_warnings", []))
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "scope": {"country": country, "disease": disease},
        "profile": profile_payload,
        "label_audit": audit_payload,
        "summary": {
            "source_count": profile.source_registry.row_count,
            "raw_document_count": profile.raw_documents.row_count,
            "event_count": profile.epidemic_events.row_count,
            "feature_count": profile.model_features.row_count,
            "prediction_count": profile.predictions.row_count,
            "feedback_count": profile.human_feedback.row_count,
            "experience_count": profile.model_experience_bank.row_count,
            "label_audit_checked_features": audit.checked_features,
            "warnings_count": len(warnings),
            "requires_human_review": True,
        },
        "warnings": warnings,
        "limitations": [
            "This data-quality report is not a guarantee of future forecasting accuracy.",
            "Label definitions and analysis results require human review before operational use.",
        ],
    }


def render_markdown_report(report: dict) -> str:
    safe_report = _safe_jsonable(report)
    profile = safe_report.get("profile", {})
    audit = safe_report.get("label_audit", {})
    warnings = safe_report.get("warnings", [])
    lines = [
        "# Data Quality Report",
        "",
        "## Summary",
        "",
        _markdown_json(safe_report.get("summary", {})),
        "",
        "## Source Registry",
        "",
        _table_summary(profile.get("source_registry", {})),
        "",
        "## Raw Documents",
        "",
        _table_summary(profile.get("raw_documents", {})),
        "",
        "## Epidemic Events",
        "",
        _table_summary(profile.get("epidemic_events", {})),
        "",
        "## Model Features",
        "",
        _table_summary(profile.get("model_features", {})),
        "",
        "## Predictions",
        "",
        _table_summary(profile.get("predictions", {})),
        "",
        "## Feedback & Experience",
        "",
        _table_summary(profile.get("human_feedback", {})),
        "",
        _table_summary(profile.get("model_experience_bank", {})),
        "",
        "## Label Audit",
        "",
        _markdown_json(
            {
                "checked_features": audit.get("checked_features"),
                "horizon_days_checked": audit.get("horizon_days_checked"),
                "summary": audit.get("summary"),
                "inconsistent_rows": audit.get("inconsistent_rows", []),
            }
        ),
        "",
        "## Warnings",
        "",
        _markdown_list(warnings),
        "",
        "## Recommended Next Actions",
        "",
        "- Review low-confidence event extraction and missing-field warnings.",
        "- Review label leakage warnings before using features for modeling.",
        "- Confirm outbreak threshold and horizon definitions with a human analyst.",
        "- Treat forecasts as auxiliary analysis; future accuracy is not guaranteed.",
        "",
        "## Limitations",
        "",
        _markdown_list(safe_report.get("limitations", [])),
        "",
    ]
    return "\n".join(lines)


def write_report_files(
    report: dict,
    *,
    output_dir: str = "reports",
    prefix: str = "data_quality",
) -> dict:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    json_path = directory / f"{prefix}_{timestamp}.json"
    md_path = directory / f"{prefix}_{timestamp}.md"
    safe_report = _safe_jsonable(report)
    json_path.write_text(json.dumps(safe_report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    md_path.write_text(render_markdown_report(safe_report), encoding="utf-8")
    return {
        "report_json": str(json_path),
        "report_markdown": str(md_path),
        "warnings_count": len(safe_report.get("warnings", [])),
        "key_counts": safe_report.get("summary", {}),
    }


def _table_summary(summary: dict[str, Any]) -> str:
    if not summary:
        return "_No data._"
    return _markdown_json(
        {
            "table_name": summary.get("table_name"),
            "row_count": summary.get("row_count"),
            "key_metrics": summary.get("key_metrics", {}),
            "warnings": summary.get("warnings", []),
        }
    )


def _markdown_json(value: Any) -> str:
    return "```json\n" + json.dumps(_safe_jsonable(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n```"


def _markdown_list(values: list[Any]) -> str:
    if not values:
        return "- none"
    return "\n".join(f"- {item}" for item in values)


def _safe_jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _safe_jsonable(asdict(value))
    if isinstance(value, dict):
        safe: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if key_text in OMITTED_FIELDS:
                safe[f"{key_text}_omitted"] = True
            else:
                safe[key_text] = _safe_jsonable(item)
        return safe
    if isinstance(value, list):
        return [_safe_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_safe_jsonable(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)
