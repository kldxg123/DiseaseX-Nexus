from __future__ import annotations

from common.db import get_session
from common.models import EpidemicEvent
from deepseek_web_search_agent.human_review_promotion import promote_reviewed_records_to_epidemic_events
from tests.deepseek_human_review_fixture import create_human_review_fixture, make_annotation_file


def test_approved_annotation_promotes_to_epidemic_event_when_persistence_allowed(tmp_path) -> None:
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
    with get_session(database_url) as session:
        event = session.query(EpidemicEvent).one()
        assert event.disease == "influenza"
        assert event.country == "United States"
        assert event.iso_week == "2026-W10"
        assert event.cases == 12
        assert event.extraction_method == "deepseek_human_review_promotion"

