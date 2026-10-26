from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select

from common.db import get_session
from common.models import DiseaseCountryInformationRecord

DEFAULT_DEEPSEEK_REPORT_DIR = "reports/deepseek_web_search/deepseek_api_diagnostics_20260708T022808Z"


@dataclass(frozen=True)
class DeepSeekReadinessReviewRow:
    record_id: int | None
    disease: str | None
    country: str | None
    source_domain: str | None
    source_title_summary: str | None
    evidence_summary: str | None
    readiness_class: str
    evidence_only: bool
    event_ready: bool
    feature_ready: bool
    visualization_ready: bool
    requires_human_review: bool
    missing_fields: list[str]
    blocker_types: list[str]
    why_not_event_ready: str
    why_not_feature_ready: str
    recommended_action: str
    source_reference_summary: str
    safety_flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DeepSeekReadinessReviewSummary:
    status: str
    total_records: int
    evidence_only_count: int
    event_ready_count: int
    feature_ready_count: int
    visualization_ready_count: int
    requires_human_review_count: int
    blocker_distribution: dict[str, int]
    top_blockers: list[str]
    limitations: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DeepSeekReadinessFilter:
    readiness: str | None = None
    blocker: str | None = None
    visualization_ready: bool | None = None
    requires_human_review: bool | None = None


@dataclass(frozen=True)
class DeepSeekHumanReviewAnnotation:
    record_id: int | None
    recommended_action: str
    reviewer_status: str = "pending"
    reviewer_notes_summary: str | None = None


@dataclass(frozen=True)
class DeepSeekReviewExport:
    status: str
    total_records: int
    review_queue_count: int
    output_json: str
    output_markdown: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_readiness_table(report_dir: str | Path = DEFAULT_DEEPSEEK_REPORT_DIR) -> list[dict[str, Any]]:
    payload = _read_json(Path(report_dir) / "deepseek_record_readiness_table.json")
    records = payload.get("records") if isinstance(payload.get("records"), list) else []
    return [record for record in records if isinstance(record, dict)]


def load_quality_audit_summary(report_dir: str | Path = DEFAULT_DEEPSEEK_REPORT_DIR) -> dict[str, Any]:
    return _read_json(Path(report_dir) / "deepseek_extraction_quality_audit.json")


def build_readiness_review_rows(report_dir: str | Path = DEFAULT_DEEPSEEK_REPORT_DIR) -> list[DeepSeekReadinessReviewRow]:
    root = Path(report_dir)
    audits = load_readiness_table(root)
    db_records = _load_database_records(root)
    rows: list[DeepSeekReadinessReviewRow] = []
    for audit in audits:
        record_id = _int_or_none(audit.get("record_id"))
        record = db_records.get(record_id, {})
        merged = {**audit, **record}
        evidence_summary = _truncate(merged.get("evidence_summary") or merged.get("text_summary"), 260)
        readiness = set(str(item) for item in (audit.get("readiness") or []))
        blockers = sorted(set(str(item) for item in (audit.get("blockers") or []) if item))
        event_ready = bool(audit.get("can_create_epidemic_event"))
        feature_ready = bool(audit.get("can_be_feature_ready"))
        visualization_ready = bool(audit.get("can_visualize"))
        evidence_only = "evidence_only" in readiness or not event_ready
        missing_fields = _missing_fields(audit)
        row = DeepSeekReadinessReviewRow(
            record_id=record_id,
            disease=_truncate(merged.get("disease"), 80),
            country=_truncate(merged.get("country"), 80),
            source_domain=_truncate(merged.get("source_domain"), 120),
            source_title_summary=_truncate(merged.get("source_title_summary") or merged.get("source_title"), 180),
            evidence_summary=evidence_summary,
            readiness_class=_readiness_class(evidence_only, event_ready, feature_ready),
            evidence_only=evidence_only,
            event_ready=event_ready,
            feature_ready=feature_ready,
            visualization_ready=visualization_ready,
            requires_human_review=bool(audit.get("requires_human_review", True)),
            missing_fields=missing_fields,
            blocker_types=blockers,
            why_not_event_ready=_why_not_event_ready(audit, missing_fields, event_ready),
            why_not_feature_ready=_why_not_feature_ready(audit, missing_fields, feature_ready),
            recommended_action=recommend_manual_review_action({"missing_fields": missing_fields, "blocker_types": blockers}),
            source_reference_summary=_source_reference_summary(merged),
            safety_flags=_safety_flags(audit),
        )
        rows.append(row)
    return rows


def summarize_readiness_review(rows: list[DeepSeekReadinessReviewRow]) -> DeepSeekReadinessReviewSummary:
    blockers: Counter[str] = Counter()
    for row in rows:
        blockers.update(row.blocker_types)
        if row.requires_human_review:
            blockers.update(["requires_human_review"])
    top_blockers = [name for name, _count in blockers.most_common(5)]
    return DeepSeekReadinessReviewSummary(
        status="ok",
        total_records=len(rows),
        evidence_only_count=sum(1 for row in rows if row.evidence_only),
        event_ready_count=sum(1 for row in rows if row.event_ready),
        feature_ready_count=sum(1 for row in rows if row.feature_ready),
        visualization_ready_count=sum(1 for row in rows if row.visualization_ready),
        requires_human_review_count=sum(1 for row in rows if row.requires_human_review),
        blocker_distribution=dict(sorted(blockers.items())),
        top_blockers=top_blockers,
        limitations=(
            "Rows are evidence and visualization review candidates. They are not promoted to epidemic events "
            "or model features without human review and stronger structured evidence."
        ),
    )


def filter_readiness_rows(
    rows: list[DeepSeekReadinessReviewRow],
    *,
    readiness: str | None = None,
    blocker: str | None = None,
    visualization_ready: bool | None = None,
    requires_human_review: bool | None = None,
) -> list[DeepSeekReadinessReviewRow]:
    filtered = rows
    if readiness:
        wanted = readiness.strip().lower()
        filtered = [
            row
            for row in filtered
            if row.readiness_class == wanted
            or (wanted == "event_ready" and row.event_ready)
            or (wanted == "feature_ready" and row.feature_ready)
            or (wanted == "visualization_ready" and row.visualization_ready)
            or (wanted == "evidence_only" and row.evidence_only)
        ]
    if blocker:
        filtered = [row for row in filtered if blocker in row.blocker_types]
    if visualization_ready is not None:
        filtered = [row for row in filtered if row.visualization_ready is bool(visualization_ready)]
    if requires_human_review is not None:
        filtered = [row for row in filtered if row.requires_human_review is bool(requires_human_review)]
    return filtered


def build_missing_field_explanation(row: DeepSeekReadinessReviewRow | dict[str, Any]) -> str:
    data = row.to_dict() if isinstance(row, DeepSeekReadinessReviewRow) else row
    fields = list(data.get("missing_fields") or [])
    if not fields:
        return "No required field gap detected by local audit; human review is still required."
    labels = {
        "date_or_iso_week": "missing explicit date or ISO week",
        "metric_name": "missing metric type",
        "numeric_value": "missing numeric value",
        "source_reference": "missing source reference",
        "evidence_summary": "missing safe evidence summary",
        "disease": "missing disease",
        "country_or_location": "missing country or location",
    }
    return "; ".join(labels.get(field, field) for field in fields)


def recommend_manual_review_action(row: DeepSeekReadinessReviewRow | dict[str, Any]) -> str:
    data = row.to_dict() if isinstance(row, DeepSeekReadinessReviewRow) else row
    missing = set(data.get("missing_fields") or [])
    blockers = set(data.get("blocker_types") or [])
    if "source_reference" in missing or "evidence_summary" in missing or "insufficient_source_grounding" in blockers:
        return "Find or confirm a stronger structured source before event extraction."
    if "date_or_iso_week" in missing and "numeric_value" in missing:
        return "Keep as evidence; search for structured data with explicit week/date and numeric values."
    if "date_or_iso_week" in missing:
        return "Human reviewer should confirm the date or ISO week before event extraction."
    if "numeric_value" in missing or "metric_name" in missing:
        return "Human reviewer should confirm metric type and numeric value before feature generation."
    return "Human reviewer can verify source grounding and decide whether to promote."


def export_human_review_queue(
    rows: list[DeepSeekReadinessReviewRow],
    output_dir: str | Path,
) -> DeepSeekReviewExport:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    queue = [
        {
            "record_id": row.record_id,
            "disease": row.disease,
            "country": row.country,
            "source_domain": row.source_domain,
            "readiness_class": row.readiness_class,
            "missing_fields": row.missing_fields,
            "blocker_types": row.blocker_types,
            "recommended_action": row.recommended_action,
            "source_reference_summary": row.source_reference_summary,
            "reviewer_status": "pending",
        }
        for row in rows
        if row.requires_human_review
    ]
    json_path = root / "deepseek_manual_review_queue.json"
    md_path = root / "deepseek_manual_review_queue.md"
    json_path.write_text(json.dumps({"queue": queue}, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_queue_markdown(md_path, queue)
    return DeepSeekReviewExport(
        status="ok",
        total_records=len(rows),
        review_queue_count=len(queue),
        output_json=str(json_path),
        output_markdown=str(md_path),
    )


def write_readiness_review_reports(report_dir: str | Path = DEFAULT_DEEPSEEK_REPORT_DIR) -> dict[str, Any]:
    root = Path(report_dir)
    rows = build_readiness_review_rows(root)
    summary = summarize_readiness_review(rows)
    export = export_human_review_queue(rows, root)
    rows_payload = {"records": [row.to_dict() for row in rows]}
    summary_payload = summary.to_dict()
    table_json = root / "deepseek_readiness_review_table.json"
    table_md = root / "deepseek_readiness_review_table.md"
    summary_json = root / "deepseek_readiness_dashboard_summary.json"
    summary_md = root / "deepseek_readiness_dashboard_summary.md"
    table_json.write_text(json.dumps(rows_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_table_markdown(table_md, rows)
    summary_json.write_text(json.dumps(summary_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_summary_markdown(summary_md, summary)
    return {
        "status": "ok",
        **summary_payload,
        "paths": {
            "readiness_review_table_json": str(table_json),
            "readiness_review_table_markdown": str(table_md),
            "manual_review_queue_json": export.output_json,
            "manual_review_queue_markdown": export.output_markdown,
            "dashboard_summary_json": str(summary_json),
            "dashboard_summary_markdown": str(summary_md),
        },
    }


def _load_database_records(report_dir: Path) -> dict[int, dict[str, Any]]:
    replay = _read_json(report_dir / "deepseek_structured_extraction_replay.json")
    run_id = str(replay.get("run_id") or "")
    db_url = _default_database_url(report_dir)
    if not run_id or not db_url:
        return {}
    try:
        with get_session(db_url) as session:
            rows = session.scalars(
                select(DiseaseCountryInformationRecord)
                .where(DiseaseCountryInformationRecord.run_id == run_id)
                .order_by(DiseaseCountryInformationRecord.id)
            ).all()
            return {int(row.id): _record_to_safe_mapping(row) for row in rows}
    except Exception:
        return {}


def _record_to_safe_mapping(row: DiseaseCountryInformationRecord) -> dict[str, Any]:
    return {
        "record_id": row.id,
        "disease": row.disease,
        "country": row.country,
        "source_domain": row.source_domain,
        "source_title": _truncate(row.source_title, 180),
        "evidence_summary": _truncate(row.evidence_summary, 260),
        "text_summary": _truncate(row.text_summary, 260),
        "event_date": row.event_date.isoformat() if row.event_date else None,
        "iso_week": row.iso_week,
        "region_name": row.region_name,
        "metric_name": row.metric_name,
        "metric_value": row.metric_value,
        "requires_human_review": row.requires_human_review,
    }


def _missing_fields(audit: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    if not audit.get("disease_present"):
        missing.append("disease")
    if not audit.get("country_present"):
        missing.append("country_or_location")
    if not audit.get("source_present"):
        missing.append("source_reference")
    if not audit.get("evidence_summary_present"):
        missing.append("evidence_summary")
    if not audit.get("temporal_signal_present"):
        missing.append("date_or_iso_week")
    if not audit.get("metric_type_present"):
        missing.append("metric_name")
    if not audit.get("numeric_value_present"):
        missing.append("numeric_value")
    return missing


def _why_not_event_ready(audit: dict[str, Any], missing_fields: list[str], event_ready: bool) -> str:
    if event_ready:
        return "Record has enough local audit fields for event-ready review, pending human confirmation."
    event_fields = [field for field in missing_fields if field in {"disease", "country_or_location", "source_reference", "evidence_summary", "date_or_iso_week"}]
    if not event_fields:
        return "Record is blocked from event extraction by conservative local audit rules."
    return "Cannot become event-ready until these fields are confirmed: " + ", ".join(event_fields) + "."


def _why_not_feature_ready(audit: dict[str, Any], missing_fields: list[str], feature_ready: bool) -> str:
    if feature_ready:
        return "Record has date, source, metric type, and numeric value for feature-ready review, pending human confirmation."
    feature_fields = [field for field in missing_fields if field in {"date_or_iso_week", "metric_name", "numeric_value", "source_reference", "evidence_summary"}]
    if not feature_fields:
        return "Record is not feature-ready because it has not passed human review or event promotion."
    return "Cannot become feature-ready until these fields are confirmed: " + ", ".join(feature_fields) + "."


def _readiness_class(evidence_only: bool, event_ready: bool, feature_ready: bool) -> str:
    if feature_ready:
        return "feature_ready"
    if event_ready:
        return "event_ready"
    if evidence_only:
        return "evidence_only"
    return "review_required"


def _source_reference_summary(row: dict[str, Any]) -> str:
    domain = _truncate(row.get("source_domain"), 120) or "unknown domain"
    title = _truncate(row.get("source_title_summary") or row.get("source_title"), 120) or "untitled source"
    record_id = row.get("record_id") or row.get("id")
    return f"record={record_id or 'unknown'}; domain={domain}; title={title}"


def _safety_flags(audit: dict[str, Any]) -> list[str]:
    flags = ["safe_summary_only", "no_page_body_included"]
    if audit.get("requires_human_review", True):
        flags.append("human_review_required")
    return flags


def _write_table_markdown(path: Path, rows: list[DeepSeekReadinessReviewRow]) -> None:
    lines = [
        "# DeepSeek Extraction Readiness Review",
        "",
        "| record_id | source_domain | readiness | missing_fields | recommended_action |",
        "|---:|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row.record_id or ''} | {row.source_domain or ''} | {row.readiness_class} | "
            f"{', '.join(row.missing_fields)} | {row.recommended_action} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_summary_markdown(path: Path, summary: DeepSeekReadinessReviewSummary) -> None:
    payload = summary.to_dict()
    lines = ["# DeepSeek Readiness Dashboard Summary", ""]
    for key, value in payload.items():
        lines.append(f"- `{key}`: `{value}`")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_queue_markdown(path: Path, queue: list[dict[str, Any]]) -> None:
    lines = [
        "# DeepSeek Manual Review Queue",
        "",
        "| record_id | source_domain | readiness | recommended_action |",
        "|---:|---|---|---|",
    ]
    for row in queue:
        lines.append(
            f"| {row.get('record_id') or ''} | {row.get('source_domain') or ''} | "
            f"{row.get('readiness_class') or ''} | {row.get('recommended_action') or ''} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _default_database_url(report_dir: Path) -> str | None:
    candidate = report_dir.parent / "deepseek_web_search.sqlite"
    if candidate.exists():
        return f"sqlite:///{candidate.as_posix()}"
    return None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _truncate(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[: limit - 3] + "..."
