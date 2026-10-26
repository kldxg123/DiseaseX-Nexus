from __future__ import annotations

import csv
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from cleaner_agent.date_normalization import iso_week_from_date
from common.db import get_session, init_db
from common.models import DiseaseCountryInformationRecord, EpidemicEvent, RawDocument, SourceRegistry
from crawler_agent.raw_document_store import calculate_content_hash
from deepseek_web_search_agent.deepseek_feature_eligibility import check_feature_eligibility_after_promotion
from deepseek_web_search_agent.readiness_review import build_readiness_review_rows

VALID_REVIEWER_DECISIONS = {"approve_event", "keep_evidence_only", "reject", "needs_more_info", ""}
VALID_METRIC_TYPES = {
    "cases",
    "deaths",
    "tests",
    "positivity_rate",
    "outbreak_count",
    "hospitalization",
    "surveillance_indicator",
}
ISO_WEEK_RE = re.compile(r"^\d{4}-W\d{2}$")


@dataclass(frozen=True)
class DeepSeekHumanReviewAnnotation:
    record_id: int | None
    reviewer_decision: str = ""
    confirmed_disease: str | None = None
    confirmed_country: str | None = None
    confirmed_admin_region: str | None = None
    confirmed_event_date: str | None = None
    confirmed_iso_week: str | None = None
    metric_type: str | None = None
    metric_value: Any | None = None
    value_unit: str | None = None
    source_grounding_confirmed: bool = False
    evidence_support_note: str | None = None
    reviewer_note: str | None = None
    reviewed_by: str = "local_reviewer"
    reviewed_at: str | None = None

    @classmethod
    def from_mapping(cls, payload: dict[str, Any]) -> "DeepSeekHumanReviewAnnotation":
        return cls(
            record_id=_int_or_none(payload.get("record_id")),
            reviewer_decision=str(payload.get("reviewer_decision") or "").strip(),
            confirmed_disease=_optional_str(payload.get("confirmed_disease")),
            confirmed_country=_optional_str(payload.get("confirmed_country")),
            confirmed_admin_region=_optional_str(payload.get("confirmed_admin_region")),
            confirmed_event_date=_optional_str(payload.get("confirmed_event_date")),
            confirmed_iso_week=_optional_str(payload.get("confirmed_iso_week")),
            metric_type=_optional_str(payload.get("metric_type")),
            metric_value=payload.get("metric_value"),
            value_unit=_optional_str(payload.get("value_unit")),
            source_grounding_confirmed=_bool(payload.get("source_grounding_confirmed")),
            evidence_support_note=_optional_str(payload.get("evidence_support_note")),
            reviewer_note=_optional_str(payload.get("reviewer_note")),
            reviewed_by=_optional_str(payload.get("reviewed_by")) or "local_reviewer",
            reviewed_at=_optional_str(payload.get("reviewed_at")),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DeepSeekAnnotationTemplateRow:
    record_id: int | None
    disease: str | None
    country: str | None
    source_domain: str | None
    source_title_summary: str | None
    evidence_summary: str | None
    readiness_class: str
    missing_fields: list[str]
    blocker_types: list[str]
    suggested_fields_to_fill: list[str]
    annotation: DeepSeekHumanReviewAnnotation

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        return payload


@dataclass(frozen=True)
class DeepSeekAnnotationImportResult:
    status: str
    imported_annotations: int
    approved_event_count: int
    blocked_count: int
    evidence_only_count: int
    rejected_count: int
    needs_more_info_count: int
    decisions: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DeepSeekEventPromotionDecision:
    record_id: int | None
    reviewer_decision: str
    gate_status: str
    blocker_reason: list[str] = field(default_factory=list)
    promoted_event_id: int | None = None
    recommended_action: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DeepSeekEventPromotionResult:
    status: str
    imported_annotations: int
    approved_event_count: int
    blocked_count: int
    promoted_epidemic_events: int
    promoted_event_ids: list[int]
    decisions: list[DeepSeekEventPromotionDecision]
    feature_generation_allowed: bool
    feature_eligibility_blockers: list[str]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["decisions"] = [decision.to_dict() for decision in self.decisions]
        return payload


@dataclass(frozen=True)
class DeepSeekPromotionGateReport:
    status: str
    report_dir: str
    output_dir: str
    annotation_file: str
    result: DeepSeekEventPromotionResult
    paths: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "report_dir": self.report_dir,
            "output_dir": self.output_dir,
            "annotation_file": self.annotation_file,
            "result": self.result.to_dict(),
            "paths": dict(self.paths),
        }


def export_human_review_template(report_dir: str | Path, output_dir: str | Path) -> dict[str, Any]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    rows = []
    for review_row in build_readiness_review_rows(report_dir):
        annotation = DeepSeekHumanReviewAnnotation(
            record_id=review_row.record_id,
            confirmed_disease=review_row.disease,
            confirmed_country=review_row.country,
        )
        rows.append(
            DeepSeekAnnotationTemplateRow(
                record_id=review_row.record_id,
                disease=review_row.disease,
                country=review_row.country,
                source_domain=review_row.source_domain,
                source_title_summary=review_row.source_title_summary,
                evidence_summary=review_row.evidence_summary,
                readiness_class=review_row.readiness_class,
                missing_fields=review_row.missing_fields,
                blocker_types=review_row.blocker_types,
                suggested_fields_to_fill=_suggested_fields(review_row.missing_fields),
                annotation=annotation,
            )
        )
    payload = {"annotations": [row.to_dict() for row in rows]}
    json_path = output / "deepseek_human_review_template.json"
    csv_path = output / "deepseek_human_review_template.csv"
    instructions_path = output / "deepseek_human_review_instructions.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_template_csv(csv_path, rows)
    _write_instructions(instructions_path)
    return {
        "status": "ok",
        "template_rows": len(rows),
        "template_json": str(json_path),
        "template_csv": str(csv_path),
        "instructions_markdown": str(instructions_path),
        "network_accessed": False,
        "deepseek_called_in_this_step": False,
    }


def import_human_review_annotations(
    annotation_file: str | Path,
) -> tuple[list[DeepSeekHumanReviewAnnotation], DeepSeekAnnotationImportResult]:
    annotations = _load_annotations(annotation_file)
    decisions = [validate_annotation_for_event_promotion(annotation).to_dict() for annotation in annotations]
    result = DeepSeekAnnotationImportResult(
        status="ok",
        imported_annotations=len(annotations),
        approved_event_count=sum(1 for item in annotations if item.reviewer_decision == "approve_event"),
        blocked_count=sum(1 for item in decisions if item["gate_status"] == "blocked"),
        evidence_only_count=sum(1 for item in annotations if item.reviewer_decision in {"", "keep_evidence_only"}),
        rejected_count=sum(1 for item in annotations if item.reviewer_decision == "reject"),
        needs_more_info_count=sum(1 for item in annotations if item.reviewer_decision == "needs_more_info"),
        decisions=decisions,
    )
    return annotations, result


def validate_annotation_for_event_promotion(
    annotation: DeepSeekHumanReviewAnnotation | dict[str, Any],
) -> DeepSeekEventPromotionDecision:
    item = annotation if isinstance(annotation, DeepSeekHumanReviewAnnotation) else DeepSeekHumanReviewAnnotation.from_mapping(annotation)
    decision = item.reviewer_decision
    blockers: list[str] = []
    if decision not in VALID_REVIEWER_DECISIONS:
        blockers.append("invalid_reviewer_decision")
    if decision != "approve_event":
        return DeepSeekEventPromotionDecision(
            record_id=item.record_id,
            reviewer_decision=decision,
            gate_status="not_promoted",
            blocker_reason=[],
            recommended_action="Keep as evidence unless a reviewer explicitly approves event promotion.",
        )
    event_date = _parse_date(item.confirmed_event_date)
    iso_week = item.confirmed_iso_week or (iso_week_from_date(event_date) if event_date else None)
    metric_value = _float_or_none(item.metric_value)
    if not item.confirmed_disease:
        blockers.append("missing_confirmed_disease")
    if not item.confirmed_country:
        blockers.append("missing_confirmed_country")
    if event_date is None and not _valid_iso_week(iso_week):
        blockers.append("missing_or_invalid_date_or_iso_week")
    if not item.metric_type or item.metric_type not in VALID_METRIC_TYPES:
        blockers.append("missing_or_invalid_metric_type")
    if metric_value is None:
        blockers.append("missing_or_invalid_metric_value")
    if not item.source_grounding_confirmed:
        blockers.append("source_grounding_not_confirmed")
    return DeepSeekEventPromotionDecision(
        record_id=item.record_id,
        reviewer_decision=decision,
        gate_status="blocked" if blockers else "approved",
        blocker_reason=blockers,
        recommended_action="Fix missing fields before promotion." if blockers else "Eligible for local event promotion.",
    )


def promote_reviewed_records_to_epidemic_events(
    *,
    report_dir: str | Path,
    annotation_file: str | Path,
    output_dir: str | Path,
    database_url: str | None = None,
    allow_persistence: bool = False,
) -> DeepSeekPromotionGateReport:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    annotations, import_result = import_human_review_annotations(annotation_file)
    decisions_by_record = {decision["record_id"]: DeepSeekEventPromotionDecision(**decision) for decision in import_result.decisions}
    promoted_ids: list[int] = []
    persisted_decisions: list[DeepSeekEventPromotionDecision] = []
    if allow_persistence:
        if not database_url:
            raise ValueError("allow_persistence requires database_url.")
        init_db(database_url).dispose()
        with get_session(database_url) as session:
            source_records = _load_source_records(session, annotations)
            for annotation in annotations:
                decision = decisions_by_record.get(annotation.record_id) or validate_annotation_for_event_promotion(annotation)
                if decision.gate_status != "approved":
                    persisted_decisions.append(decision)
                    continue
                source_record = source_records.get(annotation.record_id or -1)
                event = build_epidemic_event_from_annotation(session, annotation, source_record)
                promoted_ids.append(int(event.id))
                persisted_decisions.append(
                    DeepSeekEventPromotionDecision(
                        record_id=annotation.record_id,
                        reviewer_decision=annotation.reviewer_decision,
                        gate_status="promoted",
                        promoted_event_id=event.id,
                        recommended_action="Promoted into local pilot epidemic_events after human confirmation.",
                    )
                )
            eligibility = check_feature_eligibility_after_promotion(session, event_ids=promoted_ids)
    else:
        persisted_decisions = list(decisions_by_record.values())
        eligibility = _empty_feature_eligibility()
    result = DeepSeekEventPromotionResult(
        status="ok",
        imported_annotations=import_result.imported_annotations,
        approved_event_count=import_result.approved_event_count,
        blocked_count=sum(1 for decision in persisted_decisions if decision.gate_status == "blocked"),
        promoted_epidemic_events=len(promoted_ids),
        promoted_event_ids=promoted_ids,
        decisions=persisted_decisions,
        feature_generation_allowed=eligibility.feature_generation_allowed,
        feature_eligibility_blockers=eligibility.blocker_types,
    )
    paths = write_promotion_report(
        output,
        import_result=import_result,
        result=result,
        feature_eligibility=eligibility.to_dict(),
    )
    return DeepSeekPromotionGateReport(
        status="ok",
        report_dir=str(report_dir),
        output_dir=str(output),
        annotation_file=str(annotation_file),
        result=result,
        paths=paths,
    )


def build_epidemic_event_from_annotation(
    session: Session,
    annotation: DeepSeekHumanReviewAnnotation,
    source_record: DiseaseCountryInformationRecord | None = None,
) -> EpidemicEvent:
    event_date = _parse_date(annotation.confirmed_event_date)
    iso_week = annotation.confirmed_iso_week or (iso_week_from_date(event_date) if event_date else None)
    source = _get_or_create_review_source(session, annotation, source_record)
    document = _get_or_create_review_raw_document(session, annotation, source, source_record)
    metric_fields = _metric_fields(annotation)
    event = EpidemicEvent(
        raw_document_id=document.id,
        country=annotation.confirmed_country,
        admin1=annotation.confirmed_admin_region,
        admin2=None,
        disease=annotation.confirmed_disease,
        event_date=event_date,
        iso_week=iso_week,
        confidence_score=0.85,
        extraction_method="deepseek_human_review_promotion",
        provenance_json={
            "source": "deepseek_human_review_promotion",
            "information_record_id": annotation.record_id,
            "reviewer_decision": annotation.reviewer_decision,
            "source_grounding_confirmed": annotation.source_grounding_confirmed,
            "evidence_support_note_present": bool(annotation.evidence_support_note),
            "reviewer_note_present": bool(annotation.reviewer_note),
        },
        **metric_fields,
    )
    session.add(event)
    session.flush()
    if source_record is not None:
        source_record.epidemic_event_id = event.id
        source_record.cleaning_status = "human_review_promoted"
    session.flush()
    return event


def write_promotion_report(
    output_dir: str | Path,
    *,
    import_result: DeepSeekAnnotationImportResult,
    result: DeepSeekEventPromotionResult,
    feature_eligibility: dict[str, Any],
) -> dict[str, str]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    decisions = [decision.to_dict() for decision in result.decisions]
    paths = {
        "annotation_import_summary": str(output / "annotation_import_summary.json"),
        "annotation_validation_report": str(output / "annotation_validation_report.json"),
        "event_promotion_decisions_json": str(output / "event_promotion_decisions.json"),
        "event_promotion_decisions_md": str(output / "event_promotion_decisions.md"),
        "event_promotion_summary_json": str(output / "event_promotion_summary.json"),
        "event_promotion_summary_md": str(output / "event_promotion_summary.md"),
        "promoted_epidemic_events_summary": str(output / "promoted_epidemic_events_summary.json"),
        "feature_eligibility_json": str(output / "feature_eligibility_after_promotion.json"),
        "feature_eligibility_md": str(output / "feature_eligibility_after_promotion.md"),
        "safety_audit": str(output / "human_review_promotion_safety_audit.md"),
        "next_action_plan": str(output / "next_human_review_action_plan.md"),
    }
    _write_json(paths["annotation_import_summary"], import_result.to_dict())
    _write_json(paths["annotation_validation_report"], {"decisions": import_result.decisions})
    _write_json(paths["event_promotion_decisions_json"], {"decisions": decisions})
    _write_json(paths["event_promotion_summary_json"], result.to_dict())
    _write_json(paths["promoted_epidemic_events_summary"], {"promoted_event_ids": result.promoted_event_ids})
    _write_json(paths["feature_eligibility_json"], feature_eligibility)
    _write_md_table(paths["event_promotion_decisions_md"], decisions)
    _write_md_summary(paths["event_promotion_summary_md"], "Event Promotion Summary", result.to_dict())
    _write_md_summary(paths["feature_eligibility_md"], "Feature Eligibility After Promotion", feature_eligibility)
    Path(paths["safety_audit"]).write_text(
        "# Human Review Promotion Safety Audit\n\n"
        "- Network accessed: false\n"
        "- DeepSeek called: false\n"
        "- Tavily rerun: false\n"
        "- Candidate URLs accessed: false\n"
        "- Promotion requires reviewer_decision=approve_event and gate pass.\n",
        encoding="utf-8",
    )
    Path(paths["next_action_plan"]).write_text(
        "# Next Human Review Action Plan\n\n"
        "- Fill missing date/week, metric type, numeric value, and source grounding for records to promote.\n"
        "- Keep incomplete records as evidence-only.\n"
        "- Do not generate model features until feature eligibility passes.\n",
        encoding="utf-8",
    )
    return paths


def _load_annotations(path: str | Path) -> list[DeepSeekHumanReviewAnnotation]:
    file_path = Path(path)
    if file_path.suffix.lower() == ".csv":
        with file_path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        return [DeepSeekHumanReviewAnnotation.from_mapping(row) for row in rows]
    payload = json.loads(file_path.read_text(encoding="utf-8"))
    rows = payload.get("annotations") if isinstance(payload, dict) else payload
    annotations: list[DeepSeekHumanReviewAnnotation] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        annotation_payload = row.get("annotation") if isinstance(row.get("annotation"), dict) else row
        annotations.append(DeepSeekHumanReviewAnnotation.from_mapping(annotation_payload))
    return annotations


def _load_source_records(
    session: Session,
    annotations: list[DeepSeekHumanReviewAnnotation],
) -> dict[int, DiseaseCountryInformationRecord]:
    ids = [annotation.record_id for annotation in annotations if annotation.record_id is not None]
    if not ids:
        return {}
    rows = session.scalars(select(DiseaseCountryInformationRecord).where(DiseaseCountryInformationRecord.id.in_(ids))).all()
    return {int(row.id): row for row in rows}


def _get_or_create_review_source(
    session: Session,
    annotation: DeepSeekHumanReviewAnnotation,
    source_record: DiseaseCountryInformationRecord | None,
) -> SourceRegistry:
    base_url = source_record.source_url if source_record is not None else f"deepseek-review://record/{annotation.record_id}"
    existing = session.scalar(select(SourceRegistry).where(SourceRegistry.base_url == base_url))
    if existing is not None:
        return existing
    source = SourceRegistry(
        name=(source_record.source_title if source_record is not None and source_record.source_title else "DeepSeek Human Review Source"),
        base_url=base_url,
        country_scope=[annotation.confirmed_country or ""],
        disease_scope=[annotation.confirmed_disease or ""],
        source_type="human_reviewed_evidence",
        reliability_score=0.6,
        relevance_score=0.7,
        structured_data_score=0.35,
        parser_type="human_review_annotation",
        status="pending_review",
        metadata_json={"source": "deepseek_human_review_promotion", "requires_human_review": False},
    )
    session.add(source)
    session.flush()
    return source


def _get_or_create_review_raw_document(
    session: Session,
    annotation: DeepSeekHumanReviewAnnotation,
    source: SourceRegistry,
    source_record: DiseaseCountryInformationRecord | None,
) -> RawDocument:
    title = source_record.source_title if source_record is not None and source_record.source_title else "Human-reviewed DeepSeek evidence"
    body = "\n".join(
        [
            f"Disease: {annotation.confirmed_disease}",
            f"Country: {annotation.confirmed_country}",
            f"Event date: {annotation.confirmed_event_date or ''}",
            f"ISO week: {annotation.confirmed_iso_week or ''}",
            f"Metric: {annotation.metric_type}",
            f"Value: {_float_or_none(annotation.metric_value)} {annotation.value_unit or ''}".strip(),
            f"Source grounding confirmed: {annotation.source_grounding_confirmed}",
        ]
    )
    digest = calculate_content_hash(title, body)
    existing = session.scalar(select(RawDocument).where(RawDocument.content_hash == digest))
    if existing is not None:
        return existing
    url = source_record.source_url if source_record is not None else f"deepseek-review://record/{annotation.record_id}"
    document = RawDocument(
        source_registry_id=source.id,
        original_url=url,
        normalized_url=url,
        title=title,
        body_text=body,
        content_hash=digest,
        status_code=200,
        content_type="text/plain; charset=utf-8",
        country_detected=annotation.confirmed_country,
        disease_detected=annotation.confirmed_disease,
        extraction_status="human_reviewed",
        fetched_at=datetime.now(UTC),
        metadata_json={
            "source": "deepseek_human_review_promotion",
            "information_record_id": annotation.record_id,
            "human_review_confirmed": True,
        },
    )
    session.add(document)
    session.flush()
    return document


def _metric_fields(annotation: DeepSeekHumanReviewAnnotation) -> dict[str, Any]:
    value = _float_or_none(annotation.metric_value)
    metric_type = annotation.metric_type
    payload = {
        "cases": None,
        "suspected_cases": None,
        "confirmed_cases": None,
        "deaths": None,
        "hospitalizations": None,
        "tests": None,
        "positivity_rate": None,
        "outbreaks_count": None,
        "setting": None,
        "pathogen_genotype": None,
    }
    if value is None:
        return payload
    if metric_type == "cases":
        payload["cases"] = int(value)
        payload["confirmed_cases"] = int(value)
    elif metric_type == "deaths":
        payload["deaths"] = int(value)
    elif metric_type == "tests":
        payload["tests"] = int(value)
    elif metric_type == "positivity_rate":
        payload["positivity_rate"] = float(value)
    elif metric_type == "outbreak_count":
        payload["outbreaks_count"] = int(value)
    elif metric_type == "hospitalization":
        payload["hospitalizations"] = int(value)
    return payload


def _empty_feature_eligibility():
    class _Result:
        feature_generation_allowed = False
        blocker_types = ["persistence_not_allowed"]

        def to_dict(self):
            return {
                "promoted_event_count": 0,
                "distinct_iso_weeks": 0,
                "numeric_target_count": 0,
                "disease_country_consistent": True,
                "minimum_weeks_requirement": 4,
                "feature_generation_allowed": False,
                "blocker_types": ["persistence_not_allowed"],
                "recommended_next_action": "Run with allow_persistence=true against a local pilot DB after review.",
            }

    return _Result()


def _write_template_csv(path: Path, rows: list[DeepSeekAnnotationTemplateRow]) -> None:
    fields = [
        "record_id",
        "disease",
        "country",
        "source_domain",
        "source_title_summary",
        "evidence_summary",
        "readiness_class",
        "missing_fields",
        "blocker_types",
        "suggested_fields_to_fill",
        "reviewer_decision",
        "confirmed_disease",
        "confirmed_country",
        "confirmed_admin_region",
        "confirmed_event_date",
        "confirmed_iso_week",
        "metric_type",
        "metric_value",
        "value_unit",
        "source_grounding_confirmed",
        "evidence_support_note",
        "reviewer_note",
        "reviewed_by",
        "reviewed_at",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            payload = row.to_dict()
            annotation = payload.pop("annotation")
            writer.writerow(
                {
                    **{key: _csv_value(payload.get(key)) for key in payload},
                    **{key: _csv_value(annotation.get(key)) for key in annotation},
                }
            )


def _write_instructions(path: Path) -> None:
    path.write_text(
        "# DeepSeek Human Review Instructions\n\n"
        "Fill only fields you can confirm from approved public evidence. "
        "Use reviewer_decision=approve_event only when date/week, metric type, numeric value, disease, country, "
        "and source grounding are all confirmed. Leave incomplete records as keep_evidence_only or needs_more_info.\n",
        encoding="utf-8",
    )


def _suggested_fields(missing_fields: list[str]) -> list[str]:
    mapping = {
        "date_or_iso_week": "confirmed_event_date or confirmed_iso_week",
        "metric_name": "metric_type",
        "numeric_value": "metric_value",
        "source_reference": "source_grounding_confirmed",
        "evidence_summary": "evidence_support_note",
        "disease": "confirmed_disease",
        "country_or_location": "confirmed_country",
    }
    return [mapping.get(field, field) for field in missing_fields]


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _valid_iso_week(value: str | None) -> bool:
    if not value or not ISO_WEEK_RE.fullmatch(str(value)):
        return False
    week = int(str(value).split("-W", 1)[1])
    return 1 <= week <= 53


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None and value != "" else None
    except (TypeError, ValueError):
        return None


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def _csv_value(value: Any) -> str:
    if isinstance(value, list):
        return ";".join(str(item) for item in value)
    if value is None:
        return ""
    return str(value)


def _write_json(path: str, payload: dict[str, Any]) -> None:
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _write_md_table(path: str, decisions: list[dict[str, Any]]) -> None:
    lines = ["# Event Promotion Decisions", "", "| record_id | reviewer_decision | gate_status | blockers | promoted_event_id |", "|---:|---|---|---|---:|"]
    for decision in decisions:
        lines.append(
            f"| {decision.get('record_id') or ''} | {decision.get('reviewer_decision') or ''} | "
            f"{decision.get('gate_status') or ''} | {', '.join(decision.get('blocker_reason') or [])} | "
            f"{decision.get('promoted_event_id') or ''} |"
        )
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_md_summary(path: str, title: str, payload: dict[str, Any]) -> None:
    lines = [f"# {title}", ""]
    for key, value in payload.items():
        if key == "decisions":
            continue
        lines.append(f"- `{key}`: `{value}`")
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
