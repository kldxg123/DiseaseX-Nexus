from __future__ import annotations

from deepseek_web_search_agent.human_review_promotion import promote_reviewed_records_to_epidemic_events
from tests.deepseek_human_review_fixture import create_human_review_fixture, make_annotation_file


def test_feature_eligibility_blocks_single_promoted_event(tmp_path) -> None:
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
        allow_persistence=True,
    )

    assert report.result.promoted_epidemic_events == 1
    assert report.result.feature_generation_allowed is False
    assert "insufficient_distinct_iso_weeks" in report.result.feature_eligibility_blockers

