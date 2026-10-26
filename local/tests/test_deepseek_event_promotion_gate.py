from __future__ import annotations

from deepseek_web_search_agent.human_review_promotion import promote_reviewed_records_to_epidemic_events
from tests.deepseek_human_review_fixture import create_human_review_fixture, count_events, make_annotation_file


def test_event_promotion_gate_does_not_write_without_allow_persistence(tmp_path) -> None:
    report_dir, database_url, output_dir = create_human_review_fixture(tmp_path)
    annotation_file = make_annotation_file(
        output_dir / "deepseek_human_review_template.json",
        output_dir / "approved.json",
    )

    report = promote_reviewed_records_to_epidemic_events(
        report_dir=report_dir,
        annotation_file=annotation_file,
        output_dir=output_dir,
        database_url=database_url,
        allow_persistence=False,
    )

    assert report.result.approved_event_count == 1
    assert report.result.promoted_epidemic_events == 0
    assert count_events(database_url) == 0
    assert "persistence_not_allowed" in report.result.feature_eligibility_blockers


def test_keep_evidence_only_and_reject_do_not_promote(tmp_path) -> None:
    report_dir, database_url, output_dir = create_human_review_fixture(tmp_path)
    annotation_file = make_annotation_file(
        output_dir / "deepseek_human_review_template.json",
        output_dir / "keep.json",
        decision="keep_evidence_only",
    )
    report = promote_reviewed_records_to_epidemic_events(
        report_dir=report_dir,
        annotation_file=annotation_file,
        output_dir=output_dir,
        database_url=database_url,
        allow_persistence=True,
    )
    assert report.result.promoted_epidemic_events == 0
    assert count_events(database_url) == 0

