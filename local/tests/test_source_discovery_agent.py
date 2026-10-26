from __future__ import annotations

from common.db import get_session, init_db
from common.models import SourceRegistry
from source_discovery_agent import SourceDiscoveryAgent
from source_discovery_agent.report import discovery_report_to_dict, render_discovery_report_markdown


FIXTURE = "tests/fixtures/source_discovery_norovirus_search_results.json"


def _database_url(tmp_path) -> str:
    return f"sqlite:///{tmp_path / 'source-discovery-agent.db'}"


def test_source_discovery_agent_discovers_saves_and_generates_specs(tmp_path) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()

    with get_session(database_url) as session:
        report = SourceDiscoveryAgent(session).discover_from_fixture(
            country="United States",
            disease="norovirus",
            fixture_path=FIXTURE,
            min_source_score=0.35,
        )
        rows = session.query(SourceRegistry).all()

        assert report.status == "ok"
        assert report.search_results_seen == 8
        assert report.candidates_found == 8
        assert report.candidates_inserted_or_updated == len(rows)
        assert report.connector_specs_generated >= 3
        assert all(row.status == "pending_review" for row in rows)


def test_source_discovery_report_outputs_safe_summary(tmp_path) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()

    with get_session(database_url) as session:
        report = SourceDiscoveryAgent(session).discover_from_fixture(
            country="United States",
            disease="norovirus",
            fixture_path=FIXTURE,
        )

    payload = discovery_report_to_dict(report)
    markdown = render_discovery_report_markdown(report)

    assert payload["connector_specs_generated"] >= 1
    assert "Human Review Required" in markdown
    assert "No live search" in markdown
    assert "metadata_json" not in markdown
    assert "raw_text" not in markdown
