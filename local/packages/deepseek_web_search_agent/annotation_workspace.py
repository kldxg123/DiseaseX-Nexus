from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from deepseek_web_search_agent.human_review_promotion import (
    DeepSeekHumanReviewAnnotation,
    import_human_review_annotations,
)
from deepseek_web_search_agent.readiness_review import build_readiness_review_rows, summarize_readiness_review


@dataclass(frozen=True)
class DeepSeekAnnotationDraft:
    report_dir: str
    annotations: list[dict[str, Any]]
    synthetic_review_fixture: bool = False
    not_real_epidemiological_data: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DeepSeekAnnotationWorkspaceRow:
    record_id: int | None
    source_domain: str | None
    evidence_summary: str | None
    readiness_class: str
    missing_fields: list[str]
    blocker_types: list[str]
    suggested_review_action: str
    reviewer_decision: str = ""
    confirmed_event_date: str = ""
    confirmed_iso_week: str = ""
    metric_type: str = ""
    metric_value: str = ""
    source_grounding_confirmed: bool = False
    reviewer_note: str = ""
    gate_preview: str = "not_promoted"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DeepSeekAnnotationDraftSaveResult:
    status: str
    row_count: int
    output_path: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DeepSeekAnnotationValidationPreview:
    status: str
    imported_annotations: int
    approved_event_count: int
    blocked_count: int
    not_promoted_count: int
    decisions: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DeepSeekAnnotationWorkspaceSummary:
    status: str
    total_records: int
    evidence_only_count: int
    event_ready_count: int
    feature_ready_count: int
    visualization_ready_count: int
    requires_human_review_count: int
    output_dir: str | None = None
    paths: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_annotation_workspace_rows(report_dir: str | Path) -> list[DeepSeekAnnotationWorkspaceRow]:
    rows = []
    for row in build_readiness_review_rows(report_dir):
        rows.append(
            DeepSeekAnnotationWorkspaceRow(
                record_id=row.record_id,
                source_domain=row.source_domain,
                evidence_summary=row.evidence_summary,
                readiness_class=row.readiness_class,
                missing_fields=row.missing_fields,
                blocker_types=row.blocker_types,
                suggested_review_action=row.recommended_action,
                gate_preview="not_promoted",
            )
        )
    return rows


def create_empty_annotation_draft(report_dir: str | Path, output_dir: str | Path) -> DeepSeekAnnotationDraftSaveResult:
    rows = build_annotation_workspace_rows(report_dir)
    annotations = [
        {
            "record_id": row.record_id,
            "reviewer_decision": "",
            "confirmed_disease": "",
            "confirmed_country": "",
            "confirmed_admin_region": "",
            "confirmed_event_date": "",
            "confirmed_iso_week": "",
            "metric_type": "",
            "metric_value": "",
            "value_unit": "",
            "source_grounding_confirmed": False,
            "evidence_support_note": "",
            "reviewer_note": "",
            "reviewed_by": "local_reviewer",
            "reviewed_at": "",
        }
        for row in rows
    ]
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    path = output / "annotation_draft_template.json"
    return save_annotation_draft(annotations, path, report_dir=str(report_dir))


def save_annotation_draft(
    rows: list[dict[str, Any]] | list[DeepSeekAnnotationWorkspaceRow],
    output_path: str | Path,
    *,
    report_dir: str | None = None,
) -> DeepSeekAnnotationDraftSaveResult:
    payload_rows: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, DeepSeekAnnotationWorkspaceRow):
            payload_rows.append(
                {
                    "record_id": row.record_id,
                    "reviewer_decision": row.reviewer_decision,
                    "confirmed_event_date": row.confirmed_event_date,
                    "confirmed_iso_week": row.confirmed_iso_week,
                    "metric_type": row.metric_type,
                    "metric_value": row.metric_value,
                    "source_grounding_confirmed": row.source_grounding_confirmed,
                    "reviewer_note": row.reviewer_note,
                    "reviewed_by": "local_reviewer",
                }
            )
        else:
            payload_rows.append(dict(row))
    draft = DeepSeekAnnotationDraft(report_dir=report_dir or "", annotations=payload_rows)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(draft.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return DeepSeekAnnotationDraftSaveResult(status="ok", row_count=len(payload_rows), output_path=str(path))


def validate_annotation_draft(annotation_file: str | Path) -> DeepSeekAnnotationValidationPreview:
    annotations, result = import_human_review_annotations(annotation_file)
    decisions = result.decisions
    return DeepSeekAnnotationValidationPreview(
        status="ok",
        imported_annotations=len(annotations),
        approved_event_count=result.approved_event_count,
        blocked_count=result.blocked_count,
        not_promoted_count=sum(1 for decision in decisions if decision.get("gate_status") == "not_promoted"),
        decisions=decisions,
    )


def preview_promotion_decisions(annotation_file: str | Path) -> DeepSeekAnnotationValidationPreview:
    return validate_annotation_draft(annotation_file)


def export_annotation_workspace_bundle(report_dir: str | Path, output_dir: str | Path) -> dict[str, Any]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    rows = build_annotation_workspace_rows(report_dir)
    review_summary = summarize_readiness_review(build_readiness_review_rows(report_dir))
    rows_path = output / "annotation_workspace_rows.json"
    summary_path = output / "annotation_workspace_summary.json"
    draft_result = create_empty_annotation_draft(report_dir, output)
    validation = validate_annotation_draft(draft_result.output_path)
    validation_path = output / "annotation_validation_preview.json"
    preview_path = output / "promotion_decision_preview.json"
    manifest_path = output / "manual_review_bundle_manifest.json"
    bundle_md_path = output / "manual_review_bundle.md"
    rows_path.write_text(
        json.dumps({"rows": [row.to_dict() for row in rows]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    summary_payload = DeepSeekAnnotationWorkspaceSummary(
        status="ok",
        total_records=review_summary.total_records,
        evidence_only_count=review_summary.evidence_only_count,
        event_ready_count=review_summary.event_ready_count,
        feature_ready_count=review_summary.feature_ready_count,
        visualization_ready_count=review_summary.visualization_ready_count,
        requires_human_review_count=review_summary.requires_human_review_count,
        output_dir=str(output),
    ).to_dict()
    summary_path.write_text(json.dumps(summary_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    validation_path.write_text(json.dumps(validation.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    preview_path.write_text(json.dumps(validation.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    manifest = {
        "status": "ok",
        "network_accessed": False,
        "deepseek_called_in_this_step": False,
        "tavily_rerun_in_this_step": False,
        "paths": {
            "annotation_workspace_rows": str(rows_path),
            "annotation_workspace_summary": str(summary_path),
            "annotation_draft_template": draft_result.output_path,
            "annotation_validation_preview": str(validation_path),
            "promotion_decision_preview": str(preview_path),
            "manual_review_bundle": str(bundle_md_path),
        },
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    bundle_md_path.write_text(
        "# DeepSeek Manual Review Bundle\n\n"
        "- This bundle is local-only.\n"
        "- DeepSeek evidence is not treated as fact.\n"
        "- Human reviewers must confirm date/week, numeric value, metric type, and source grounding before promotion.\n"
        "- Feature generation remains blocked until minimum data gates pass.\n",
        encoding="utf-8",
    )
    return {
        **summary_payload,
        "paths": manifest["paths"],
        "manual_review_bundle_manifest": str(manifest_path),
    }
