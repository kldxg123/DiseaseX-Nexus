from __future__ import annotations

from deepseek_web_search_agent.human_review_promotion import (
    import_human_review_annotations,
    validate_annotation_for_event_promotion,
)
from tests.deepseek_human_review_fixture import create_human_review_fixture, make_annotation_file


def test_unfilled_template_import_keeps_records_not_promoted(tmp_path) -> None:
    _report_dir, _database_url, output_dir = create_human_review_fixture(tmp_path)
    template = output_dir / "deepseek_human_review_template.json"

    annotations, result = import_human_review_annotations(template)

    assert len(annotations) == 3
    assert result.imported_annotations == 3
    assert result.approved_event_count == 0
    assert result.evidence_only_count == 3
    assert all(decision["gate_status"] == "not_promoted" for decision in result.decisions)


def test_missing_required_annotation_fields_are_blocked(tmp_path) -> None:
    _report_dir, _database_url, output_dir = create_human_review_fixture(tmp_path)
    no_date = make_annotation_file(output_dir / "deepseek_human_review_template.json", output_dir / "no-date.json", include_date=False)
    no_value = make_annotation_file(output_dir / "deepseek_human_review_template.json", output_dir / "no-value.json", include_value=False)
    no_source = make_annotation_file(output_dir / "deepseek_human_review_template.json", output_dir / "no-source.json", source_grounded=False)

    for path, blocker in [
        (no_date, "missing_or_invalid_date_or_iso_week"),
        (no_value, "missing_or_invalid_metric_value"),
        (no_source, "source_grounding_not_confirmed"),
    ]:
        annotations, _result = import_human_review_annotations(path)
        decision = validate_annotation_for_event_promotion(annotations[0])
        assert decision.gate_status == "blocked"
        assert blocker in decision.blocker_reason

