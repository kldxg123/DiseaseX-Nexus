from __future__ import annotations

import json

from common.db import get_session, init_db
from common.models import EpidemicEvent, ModelFeature, RawDocument, SourceRegistry
from worker_agent import InMemoryJobRegistry, TaskRunner


def _database_url(tmp_path) -> str:
    return f"sqlite:///{(tmp_path / 'worker-tasks.db').as_posix()}"


def _html() -> str:
    return (
        "<html><title>Worker Fixture</title><body>"
        "On 2025-01-06, United States reported 12 confirmed cases and 1 outbreaks of norovirus."
        "</body></html>"
    )


def test_task_runner_seed_sources_and_unknown_task(tmp_path) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()
    with get_session(database_url) as session:
        runner = TaskRunner(session)
        result = runner.run_task("seed_sources")

        assert result["status"] == "success"
        assert result["job"]["summary"]["sources_count"] > 0
        assert session.query(SourceRegistry).count() > 0

        try:
            runner.run_task("missing_task")
        except ValueError as exc:
            assert "Unknown task" in str(exc)
        else:
            raise AssertionError("missing task should raise ValueError")


def test_task_runner_mock_crawl_requires_html_and_can_clean(tmp_path) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()
    with get_session(database_url) as session:
        runner = TaskRunner(session)
        runner.seed_sources()
        source_id = session.query(SourceRegistry.id).first()[0]

        failed = runner.run_task("mock_crawl_url", source_id=str(source_id), url="https://fixture.invalid/no-html")
        assert failed["status"] == "failed"
        assert "<html>" not in json.dumps(failed, ensure_ascii=False).lower()

        summary = runner.mock_crawl_url(
            source_id=str(source_id),
            url="https://fixture.invalid/worker-fixture",
            html=_html(),
            country_detected="United States",
            disease_detected="norovirus",
        )
        assert summary["saved"] is True
        assert "content_hash_prefix" in summary
        assert session.query(RawDocument).count() == 1

        clean_summary = runner.clean_pending(country="United States", disease="norovirus")
        assert clean_summary["processed"] == 1
        assert session.query(EpidemicEvent).count() == 1


def test_task_runner_feature_predictor_and_minimum_data_empty_safe(tmp_path) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()
    with get_session(database_url) as session:
        runner = TaskRunner(session)

        assert runner.build_features(country="United States", disease="norovirus")["feature_rows"] == 0
        backtest = runner.backtest_model(country="United States", disease="norovirus", horizon_days=28)
        prediction = runner.predict_latest(country="United States", disease="norovirus", horizon_days=28)
        minimum_data = runner.minimum_data_analysis(country="United States", disease="norovirus", horizon_days=28)

        assert backtest["features_seen"] == 0
        assert prediction["prediction_rows"] == 0
        assert minimum_data["learning_curve_count"] == 0
        assert session.query(ModelFeature).count() == 0


def test_task_runner_discovery_and_summaries_are_local(tmp_path) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()
    with get_session(database_url) as session:
        runner = TaskRunner(session)
        discovery = runner.discover_gdelt_mock(
            country="United States",
            disease="norovirus",
            min_source_score=0.1,
            mock_response_json={
                "articles": [
                    {
                        "url": "https://local-news.invalid/a",
                        "title": "fixture",
                        "sourceCommonName": "Local News",
                    },
                    {
                        "url": "https://www.local-news.invalid/b",
                        "title": "fixture",
                        "sourceCommonName": "Local News",
                    },
                ]
            },
        )

        assert discovery["upserted"] == 1
        assert runner.summarize_feedback()["feedback_count"] == 0
        assert runner.summarize_experiences()["experience_count"] == 0


def test_task_runner_parse_official_fixture_requires_content(tmp_path) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()
    with get_session(database_url) as session:
        runner = TaskRunner(session)
        result = runner.run_task("parse_official_fixture", connector="who_disease_outbreak_news")

        assert result["status"] == "failed"
        rendered = json.dumps(result, ensure_ascii=False).lower()
        assert "<html>" not in rendered
        assert "password" not in rendered
