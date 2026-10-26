from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from common.db import get_session
from common.models import DiseaseCountryInformationRecord


READINESS_LABELS = {
    "evidence_only",
    "event_ready",
    "feature_ready",
    "visualization_ready",
    "insufficient_source_grounding",
    "insufficient_temporal_signal",
    "insufficient_numeric_signal",
    "requires_human_review",
}

FORBIDDEN_OUTPUT_KEYS = {
    "raw_text",
    "html",
    "prompt",
    "completion",
    "response_body",
    "metadata_json",
    "provenance_json",
    "feature_json",
    "lesson_json",
}


@dataclass(frozen=True)
class DeepSeekExtractedRecordAudit:
    record_index: int
    record_id: int | None
    source_domain: str | None
    source_title_summary: str | None
    disease_present: bool
    country_present: bool
    source_present: bool
    evidence_summary_present: bool
    temporal_signal_present: bool
    metric_type_present: bool
    numeric_value_present: bool
    value_grounded_in_evidence: bool
    requires_human_review: bool
    readiness: list[str]
    blockers: list[str] = field(default_factory=list)
    can_create_epidemic_event: bool = False
    can_be_feature_ready: bool = False
    can_visualize: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DeepSeekReplayQualitySummary:
    status: str
    records_extracted: int
    records_audited: int
    evidence_only_count: int
    event_ready_count: int
    feature_ready_count: int
    visualization_ready_count: int
    requires_human_review_count: int
    missing_required_field_count: int
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DeepSeekPersistenceVerification:
    status: str
    run_id: str | None
    records_extracted_count: int
    records_persisted_count: int
    database_records_count: int | None
    persistence_match: bool
    raw_documents_created: int
    epidemic_events_created: int
    feature_ready_records: int
    visualization_points: int
    mismatch_warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DeepSeekCleaningBridgeVerification:
    status: str
    raw_documents_created: int
    epidemic_events_created: int
    feature_ready_records: int
    evidence_only_records: int
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DeepSeekVisualizationAudit:
    status: str
    point_count: int
    safe_field_check_passed: bool
    visualization_limited: bool
    missing_field_counts: dict[str, int] = field(default_factory=dict)
    forbidden_field_hits: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_deepseek_replay_outputs(report_dir: str | Path) -> dict[str, Any]:
    root = Path(report_dir)
    return {
        "report_dir": str(root),
        "replay": _read_json(root / "deepseek_structured_extraction_replay.json"),
        "database": _read_json(root / "database_persistence_summary.json"),
        "cleaning": _read_json(root / "cleaning_bridge_summary.json"),
        "visualization": _read_json(root / "visualization_3d_data.json"),
    }


def audit_extracted_records(records: list[Any]) -> tuple[list[DeepSeekExtractedRecordAudit], DeepSeekReplayQualitySummary]:
    audits = [classify_record_readiness(record, index=index) for index, record in enumerate(records, start=1)]
    summary = DeepSeekReplayQualitySummary(
        status="ok",
        records_extracted=len(records),
        records_audited=len(audits),
        evidence_only_count=_count_with(audits, "evidence_only"),
        event_ready_count=_count_with(audits, "event_ready"),
        feature_ready_count=_count_with(audits, "feature_ready"),
        visualization_ready_count=_count_with(audits, "visualization_ready"),
        requires_human_review_count=_count_with(audits, "requires_human_review"),
        missing_required_field_count=sum(1 for audit in audits if audit.blockers),
        warnings=[] if audits else ["no_records_to_audit"],
    )
    return audits, summary


def verify_persisted_records(
    report_dir: str | Path,
    database_url: str | None = None,
) -> DeepSeekPersistenceVerification:
    outputs = load_deepseek_replay_outputs(report_dir)
    replay = outputs["replay"]
    database = outputs["database"]
    visualization = outputs["visualization"]
    run_id = str(replay.get("run_id") or database.get("run_id") or "") or None
    records_extracted = int(replay.get("records_extracted_by_deepseek") or 0)
    records_persisted = int(database.get("records_persisted") or replay.get("records_persisted") or 0)
    db_records = _count_database_records(report_dir, run_id, database_url)
    warnings: list[str] = []
    if db_records is None:
        warnings.append("database_record_count_unavailable")
    elif db_records != records_persisted:
        warnings.append("database_record_count_mismatch")
    if records_extracted and records_extracted != records_persisted:
        warnings.append("extracted_and_persisted_count_mismatch")
    persistence_match = bool(records_extracted == records_persisted and (db_records is None or db_records == records_persisted))
    return DeepSeekPersistenceVerification(
        status="ok" if persistence_match else "mismatch",
        run_id=run_id,
        records_extracted_count=records_extracted,
        records_persisted_count=records_persisted,
        database_records_count=db_records,
        persistence_match=persistence_match,
        raw_documents_created=int(database.get("raw_documents_created") or replay.get("raw_documents_created") or 0),
        epidemic_events_created=int(database.get("epidemic_events_created") or replay.get("epidemic_events_created") or 0),
        feature_ready_records=int(replay.get("feature_ready_records") or 0),
        visualization_points=len(visualization.get("points") or []),
        mismatch_warnings=warnings,
    )


def audit_cleaning_bridge_outputs(report_dir: str | Path) -> DeepSeekCleaningBridgeVerification:
    outputs = load_deepseek_replay_outputs(report_dir)
    cleaning = outputs["cleaning"]
    raw_documents = int(cleaning.get("raw_documents_created") or 0)
    events = int(cleaning.get("epidemic_events_created") or 0)
    feature_ready = int(cleaning.get("feature_ready_records") or 0)
    evidence_only = int(cleaning.get("evidence_only_records") or 0)
    blockers: list[str] = []
    if raw_documents == 0:
        blockers.append("evidence_only_record")
    if events == 0:
        blockers.extend(["insufficient_temporal_signal", "insufficient_numeric_signal"])
    if feature_ready == 0:
        blockers.append("requires_human_review")
    return DeepSeekCleaningBridgeVerification(
        status="ok" if raw_documents or events or feature_ready else "limited",
        raw_documents_created=raw_documents,
        epidemic_events_created=events,
        feature_ready_records=feature_ready,
        evidence_only_records=evidence_only,
        blockers=sorted(set(blockers)),
        warnings=[] if cleaning else ["cleaning_bridge_summary_missing"],
    )


def audit_visualization_points(report_dir: str | Path) -> DeepSeekVisualizationAudit:
    payload = load_deepseek_replay_outputs(report_dir)["visualization"]
    points = payload.get("points") if isinstance(payload.get("points"), list) else []
    required = ["source_domain", "information_type", "x", "y", "z"]
    missing: dict[str, int] = {key: 0 for key in required}
    forbidden_hits: list[str] = []
    for point in points:
        if not isinstance(point, dict):
            continue
        for key in required:
            if point.get(key) in {None, ""}:
                missing[key] += 1
        forbidden_hits.extend(sorted(set(point).intersection(FORBIDDEN_OUTPUT_KEYS)))
    missing = {key: value for key, value in missing.items() if value}
    visualization_limited = bool(missing or payload.get("warnings"))
    return DeepSeekVisualizationAudit(
        status="ok" if not forbidden_hits else "unsafe",
        point_count=len(points),
        safe_field_check_passed=not forbidden_hits,
        visualization_limited=visualization_limited,
        missing_field_counts=missing,
        forbidden_field_hits=sorted(set(forbidden_hits)),
        warnings=list(payload.get("warnings") or []),
    )


def classify_record_readiness(record: Any, *, index: int = 1) -> DeepSeekExtractedRecordAudit:
    row = _record_to_mapping(record)
    disease_present = bool(row.get("disease"))
    country_present = bool(row.get("country") or row.get("region_name"))
    source_present = bool(row.get("source_url") or row.get("source_domain"))
    evidence_summary = row.get("evidence_summary") or row.get("text_summary")
    evidence_summary_present = bool(evidence_summary)
    temporal_signal_present = bool(row.get("event_date") or row.get("iso_week") or row.get("date_hint"))
    metric_type_present = bool(row.get("metric_name") or row.get("metric_type"))
    numeric_value_present = _is_number(row.get("metric_value") if "metric_value" in row else row.get("numeric_value"))
    requires_review = bool(row.get("requires_human_review", True))
    blockers: list[str] = []
    if not source_present or not evidence_summary_present:
        blockers.append("insufficient_source_grounding")
    if not temporal_signal_present:
        blockers.append("insufficient_temporal_signal")
    if not metric_type_present or not numeric_value_present:
        blockers.append("insufficient_numeric_signal")
    event_ready = bool(disease_present and country_present and source_present and evidence_summary_present and temporal_signal_present)
    feature_ready = bool(event_ready and metric_type_present and numeric_value_present)
    visualization_ready = bool(disease_present and country_present and source_present and evidence_summary_present)
    readiness = ["requires_human_review"] if requires_review else []
    if feature_ready:
        readiness.append("feature_ready")
    if event_ready:
        readiness.append("event_ready")
    if visualization_ready:
        readiness.append("visualization_ready")
    if not event_ready:
        readiness.append("evidence_only")
    readiness.extend(blockers)
    readiness = [label for label in sorted(set(readiness)) if label in READINESS_LABELS]
    value_grounded = bool(numeric_value_present and evidence_summary_present)
    return DeepSeekExtractedRecordAudit(
        record_index=index,
        record_id=_int_or_none(row.get("id")),
        source_domain=_truncate(row.get("source_domain"), 160),
        source_title_summary=_truncate(row.get("source_title"), 180),
        disease_present=disease_present,
        country_present=country_present,
        source_present=source_present,
        evidence_summary_present=evidence_summary_present,
        temporal_signal_present=temporal_signal_present,
        metric_type_present=metric_type_present,
        numeric_value_present=numeric_value_present,
        value_grounded_in_evidence=value_grounded,
        requires_human_review=requires_review,
        readiness=readiness,
        blockers=sorted(set(blockers)),
        can_create_epidemic_event=event_ready,
        can_be_feature_ready=feature_ready,
        can_visualize=visualization_ready,
    )


def write_deepseek_quality_audit_report(
    report_dir: str | Path,
    *,
    database_url: str | None = None,
) -> dict[str, Any]:
    root = Path(report_dir)
    root.mkdir(parents=True, exist_ok=True)
    outputs = load_deepseek_replay_outputs(root)
    persistence = verify_persisted_records(root, database_url=database_url)
    records = _load_records_for_report(root, persistence.run_id, database_url)
    record_audits, quality = audit_extracted_records(records)
    cleaning = audit_cleaning_bridge_outputs(root)
    visualization = audit_visualization_points(root)
    missing_inputs = _missing_replay_input_files(root)
    warnings = [*quality.warnings, *([f"missing_input:{name}" for name in missing_inputs])]
    summary = {
        "deepseek_currently_available": outputs["replay"].get("status") == "ok",
        "deepseek_called_in_this_step": False,
        "tavily_rerun_in_this_step": False,
        "candidate_urls_accessed": False,
        **quality.to_dict(),
        "status": "limited" if missing_inputs else quality.status,
        "warnings": warnings,
        "records_persisted": persistence.records_persisted_count,
        "persistence_match": persistence.persistence_match,
        "visualization_points": visualization.point_count,
        "cleaning_status": cleaning.status,
        "limitations": (
            "Audit uses existing local replay outputs and local persistence only; all extracted records require human review."
        ),
    }
    paths = _write_report_files(
        root,
        summary=summary,
        record_audits=record_audits,
        persistence=persistence,
        cleaning=cleaning,
        visualization=visualization,
    )
    return {
        **summary,
        "paths": paths,
        "persistence": persistence.to_dict(),
        "cleaning": cleaning.to_dict(),
        "visualization": visualization.to_dict(),
    }


def _load_records_for_report(
    report_dir: Path,
    run_id: str | None,
    database_url: str | None,
) -> list[DiseaseCountryInformationRecord]:
    if not run_id:
        return []
    url = database_url or _default_database_url(report_dir)
    if not url:
        return []
    with get_session(url) as session:
        return list(
            session.scalars(
                select(DiseaseCountryInformationRecord)
                .where(DiseaseCountryInformationRecord.run_id == run_id)
                .order_by(DiseaseCountryInformationRecord.id)
            ).all()
        )


def _write_report_files(
    root: Path,
    *,
    summary: dict[str, Any],
    record_audits: list[DeepSeekExtractedRecordAudit],
    persistence: DeepSeekPersistenceVerification,
    cleaning: DeepSeekCleaningBridgeVerification,
    visualization: DeepSeekVisualizationAudit,
) -> dict[str, str]:
    root.mkdir(parents=True, exist_ok=True)
    table = [audit.to_dict() for audit in record_audits]
    payloads = {
        "deepseek_extraction_quality_audit.json": summary,
        "deepseek_persistence_verification.json": persistence.to_dict(),
        "deepseek_cleaning_bridge_verification.json": cleaning.to_dict(),
        "deepseek_visualization_audit.json": visualization.to_dict(),
        "deepseek_record_readiness_table.json": {"records": table},
    }
    for name, payload in payloads.items():
        (root / name).write_text(json.dumps(_safe_json(payload), ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    _write_markdown(root / "deepseek_extraction_quality_audit.md", "DeepSeek Extraction Quality Audit", summary)
    _write_readiness_markdown(root / "deepseek_record_readiness_table.md", table)
    _write_next_plan(root / "deepseek_next_replay_plan.md", summary, persistence, cleaning, visualization)
    return {
        "audit_json": str(root / "deepseek_extraction_quality_audit.json"),
        "audit_markdown": str(root / "deepseek_extraction_quality_audit.md"),
        "persistence_json": str(root / "deepseek_persistence_verification.json"),
        "cleaning_json": str(root / "deepseek_cleaning_bridge_verification.json"),
        "visualization_json": str(root / "deepseek_visualization_audit.json"),
        "readiness_json": str(root / "deepseek_record_readiness_table.json"),
        "readiness_markdown": str(root / "deepseek_record_readiness_table.md"),
        "next_replay_plan": str(root / "deepseek_next_replay_plan.md"),
    }


def _missing_replay_input_files(root: Path) -> list[str]:
    expected = (
        "deepseek_structured_extraction_replay.json",
        "database_persistence_summary.json",
        "cleaning_bridge_summary.json",
        "visualization_3d_data.json",
    )
    return [name for name in expected if not (root / name).exists()]


def _write_markdown(path: Path, title: str, payload: dict[str, Any]) -> None:
    lines = [f"# {title}", ""]
    for key, value in _safe_json(payload).items():
        lines.append(f"- `{key}`: `{value}`")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_readiness_markdown(path: Path, rows: list[dict[str, Any]]) -> None:
    lines = [
        "# DeepSeek Record Readiness Table",
        "",
        "| # | source_domain | readiness | blockers | feature_ready | visualization_ready |",
        "|---:|---|---|---|---:|---:|",
    ]
    for row in rows:
        lines.append(
            "| {idx} | {domain} | {readiness} | {blockers} | {feature} | {viz} |".format(
                idx=row.get("record_index"),
                domain=row.get("source_domain") or "",
                readiness=", ".join(row.get("readiness") or []),
                blockers=", ".join(row.get("blockers") or []),
                feature=row.get("can_be_feature_ready"),
                viz=row.get("can_visualize"),
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_next_plan(
    path: Path,
    summary: dict[str, Any],
    persistence: DeepSeekPersistenceVerification,
    cleaning: DeepSeekCleaningBridgeVerification,
    visualization: DeepSeekVisualizationAudit,
) -> None:
    lines = [
        "# DeepSeek Next Replay Plan",
        "",
        "- DeepSeek API is available according to the existing replay report; this audit did not call it again.",
        "- Tavily/general search was not rerun; candidate URLs were not opened.",
        "- Keep all records in human review before using them for decisions.",
        f"- Records persisted match local database: {persistence.persistence_match}.",
        f"- Event-ready records: {summary.get('event_ready_count')}; feature-ready records: {summary.get('feature_ready_count')}.",
        f"- Cleaning bridge status: {cleaning.status}; blockers: {', '.join(cleaning.blockers) or 'none'}.",
        f"- Visualization points: {visualization.point_count}; limited: {visualization.visualization_limited}.",
        "- Next improvement: prefer structured sources with explicit dates, locations, numeric metrics, and source provenance.",
        "- A future DeepSeek replay should remain explicitly approved, bounded, and followed by this audit before dashboard promotion.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _count_database_records(report_dir: str | Path, run_id: str | None, database_url: str | None) -> int | None:
    if not run_id:
        return None
    url = database_url or _default_database_url(report_dir)
    if not url:
        return None
    try:
        with get_session(url) as session:
            return int(
                session.scalar(
                    select(func.count(DiseaseCountryInformationRecord.id)).where(
                        DiseaseCountryInformationRecord.run_id == run_id
                    )
                )
                or 0
            )
    except Exception:
        return None


def _default_database_url(report_dir: str | Path) -> str | None:
    root = Path(report_dir)
    candidate = root.parent / "deepseek_web_search.sqlite"
    if candidate.exists():
        return f"sqlite:///{candidate.as_posix()}"
    return None


def _count_with(audits: list[DeepSeekExtractedRecordAudit], label: str) -> int:
    return sum(1 for audit in audits if label in audit.readiness)


def _record_to_mapping(record: Any) -> dict[str, Any]:
    if isinstance(record, dict):
        return dict(record)
    return {
        "id": getattr(record, "id", None),
        "disease": getattr(record, "disease", None),
        "country": getattr(record, "country", None),
        "source_url": getattr(record, "source_url", None),
        "source_domain": getattr(record, "source_domain", None),
        "source_title": getattr(record, "source_title", None),
        "event_date": getattr(record, "event_date", None),
        "iso_week": getattr(record, "iso_week", None),
        "region_name": getattr(record, "region_name", None),
        "metric_name": getattr(record, "metric_name", None),
        "metric_value": getattr(record, "metric_value", None),
        "text_summary": getattr(record, "text_summary", None),
        "evidence_summary": getattr(record, "evidence_summary", None),
        "requires_human_review": getattr(record, "requires_human_review", True),
        "feature_ready_potential": getattr(record, "feature_ready_potential", False),
    }


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _safe_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _safe_json(item) for key, item in value.items() if str(key).lower() not in FORBIDDEN_OUTPUT_KEYS}
    if isinstance(value, list):
        return [_safe_json(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return _truncate(value, 500) if isinstance(value, str) else value
    return _truncate(str(value), 500)


def _is_number(value: Any) -> bool:
    if value is None or value == "":
        return False
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


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
