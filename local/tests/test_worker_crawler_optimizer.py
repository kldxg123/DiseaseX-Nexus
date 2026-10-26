from __future__ import annotations

import json

from common.db import get_session
from crawler_optimizer_agent import CrawlerOptimizerAgent
from tests.crawler_optimizer_test_utils import populated_optimizer_db
from worker_agent import TaskRunner, get_default_schedule, get_default_schedules
from worker_agent.cli import main


FORBIDDEN = (
    "raw_text",
    "raw_html",
    "<html>",
    "feature_json",
    "lesson_json",
    "provenance_json",
    "metadata_json",
    "secret",
    "token",
    "password",
    "ssh_key_path",
)


def test_task_runner_optimize_crawlers_returns_safe_summary(tmp_path) -> None:
    database_url = populated_optimizer_db(tmp_path)
    with get_session(database_url) as session:
        result = TaskRunner(session).run_task(
            "optimize_crawlers",
            country="United States",
            disease="norovirus",
        )

    assert result["status"] == "success"
    summary = result["job"]["summary"]
    assert summary["failure_case_count"] >= 1
    assert summary["recommended_action_count"] >= 1
    assert summary["connector_spec_count"] >= 1
    _assert_safe(result)


def test_worker_cli_run_task_optimize_crawlers_accepts_country_disease(tmp_path, capsys) -> None:
    database_url = populated_optimizer_db(tmp_path)

    result = main(
        [
            "run-task",
            "--database-url",
            database_url,
            "--task",
            "optimize_crawlers",
            "--country",
            "United States",
            "--disease",
            "norovirus",
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert result == 0
    assert payload["result"]["status"] == "success"
    assert payload["result"]["job"]["summary"]["failure_case_count"] >= 1
    _assert_safe(payload)


def test_worker_schedule_contains_daily_crawler_optimization() -> None:
    entries = {entry.job_name: entry for entry in get_default_schedules()}
    described = {entry["name"]: entry for entry in get_default_schedule()}

    assert entries["optimize_crawlers"].enabled is True
    assert described["daily_crawler_optimization"]["task_name"] == "optimize_crawlers"
    assert described["daily_crawler_optimization"]["enabled"] is True
    assert "does not fetch" in described["daily_crawler_optimization"]["description"]


def test_optimizer_agent_import_used_without_network(tmp_path) -> None:
    database_url = populated_optimizer_db(tmp_path)
    with get_session(database_url) as session:
        report = CrawlerOptimizerAgent(session).analyze(country="United States", disease="norovirus")

    assert report.status == "ok"
    assert report.sources_analyzed >= 1
    assert report.documents_analyzed >= 1


def _assert_safe(payload: dict) -> None:
    rendered = json.dumps(payload, ensure_ascii=False).lower()
    for marker in FORBIDDEN:
        assert marker.lower() not in rendered
