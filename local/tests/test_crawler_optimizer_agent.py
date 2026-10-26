from __future__ import annotations

from common.db import get_session
from common.models import SourceRegistry
from crawler_optimizer_agent import CrawlerOptimizerAgent
from tests.crawler_optimizer_test_utils import populated_optimizer_db


def test_optimizer_agent_analyze_returns_safe_report(tmp_path) -> None:
    database_url = populated_optimizer_db(tmp_path)
    with get_session(database_url) as session:
        report = CrawlerOptimizerAgent(session).analyze(country="United States", disease="norovirus")
        payload = report.to_dict()

    assert payload["status"] == "ok"
    assert payload["sources_analyzed"] >= 2
    assert payload["documents_analyzed"] >= 3
    assert payload["failure_cases"]
    assert payload["recommended_actions"]
    assert payload["connector_specs"]
    assert "failures" in payload["summary"]


def test_analyze_default_does_not_modify_database(tmp_path) -> None:
    database_url = populated_optimizer_db(tmp_path)
    with get_session(database_url) as session:
        before = [(source.id, source.status, source.relevance_score) for source in session.query(SourceRegistry).all()]
        CrawlerOptimizerAgent(session).analyze(country="United States", disease="norovirus")
        after = [(source.id, source.status, source.relevance_score) for source in session.query(SourceRegistry).all()]

    assert before == after


def test_apply_safe_actions_does_not_auto_disable_source(tmp_path) -> None:
    database_url = populated_optimizer_db(tmp_path)
    with get_session(database_url) as session:
        source = session.query(SourceRegistry).filter(SourceRegistry.status == "degraded").first()
        assert source is not None
        source.consecutive_failures = 15
        report = CrawlerOptimizerAgent(session).analyze(apply_safe_actions=True)
        session.flush()
        refreshed = session.get(SourceRegistry, source.id)

    assert report.summary["safe_actions_applied"] >= 0
    assert refreshed.status != "disabled"


def test_analyze_source_returns_connector_spec(tmp_path) -> None:
    database_url = populated_optimizer_db(tmp_path)
    with get_session(database_url) as session:
        source = session.query(SourceRegistry).first()
        report = CrawlerOptimizerAgent(session).analyze_source(str(source.id), disease="norovirus")

    assert report.status == "ok"
    assert report.sources_analyzed == 1
    assert report.connector_specs
