from __future__ import annotations

from deepseek_web_search_agent.annotation_workspace import (
    build_annotation_workspace_rows,
    create_empty_annotation_draft,
    export_annotation_workspace_bundle,
    preview_promotion_decisions,
    validate_annotation_draft,
)
from tests.deepseek_human_review_fixture import create_human_review_fixture, make_annotation_file


def test_annotation_workspace_rows_and_draft_validation(tmp_path) -> None:
    report_dir, _database_url, output_dir = create_human_review_fixture(tmp_path)

    rows = build_annotation_workspace_rows(report_dir)
    draft = create_empty_annotation_draft(report_dir, output_dir)
    validation = validate_annotation_draft(draft.output_path)

    assert len(rows) == 3
    assert draft.row_count == 3
    assert validation.imported_annotations == 3
    assert validation.not_promoted_count == 3
    assert rows[0].reviewer_decision == ""
    assert rows[0].gate_preview == "not_promoted"


def test_annotation_workspace_preview_detects_missing_date_and_value(tmp_path) -> None:
    report_dir, _database_url, output_dir = create_human_review_fixture(tmp_path)
    annotation_file = make_annotation_file(
        output_dir / "deepseek_human_review_template.json",
        output_dir / "missing-date.json",
        include_date=False,
    )

    preview = preview_promotion_decisions(annotation_file)
    blocked = [decision for decision in preview.decisions if decision["gate_status"] == "blocked"]
    bundle = export_annotation_workspace_bundle(report_dir, output_dir / "bundle")

    assert preview.imported_annotations == 3
    assert blocked
    assert "missing_or_invalid_date_or_iso_week" in blocked[0]["blocker_reason"]
    assert bundle["total_records"] == 3
    assert bundle["paths"]["manual_review_bundle"]

