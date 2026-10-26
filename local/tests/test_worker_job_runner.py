from __future__ import annotations

import json

from common.db import init_db
from worker_agent import JobRunner


SENSITIVE_MARKERS = ["服务器信息.txt", "secret", "token", "password", "private key"]


def _database_url(tmp_path) -> str:
    return f"sqlite:///{(tmp_path / 'worker-runner.db').as_posix()}"


def test_job_runner_runs_seed_sources(tmp_path) -> None:
    runner = JobRunner(_database_url(tmp_path))

    result = runner.run_job("seed_sources")

    assert result.status == "success"
    assert result.summary["sources_count"] > 0
    assert result.summary["inserted_or_updated"] > 0


def test_job_runner_clean_pending_empty_data_does_not_crash(tmp_path) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()
    runner = JobRunner(database_url)

    result = runner.run_job("clean_pending", {"limit": 10})

    assert result.status == "success"
    assert result.summary["processed"] == 0
    assert result.summary["events_created"] == 0


def test_job_runner_run_many_continues_after_failure(tmp_path) -> None:
    runner = JobRunner(_database_url(tmp_path))

    results = runner.run_many(
        [
            {"name": "missing_job"},
            {"name": "seed_sources"},
        ]
    )

    assert [result.status for result in results] == ["failed", "success"]
    assert results[1].summary["sources_count"] > 0


def test_mock_source_discovery_without_mock_input_is_skipped(tmp_path) -> None:
    runner = JobRunner(_database_url(tmp_path))

    result = runner.run_job("mock_source_discovery")

    assert result.status == "skipped"
    assert result.summary["upserted"] == 0


def test_minimum_data_analysis_empty_data_does_not_crash(tmp_path) -> None:
    runner = JobRunner(_database_url(tmp_path))

    result = runner.run_job("minimum_data_analysis")

    assert result.status == "success"
    assert result.summary["learning_curve_count"] == 0


def test_smoke_test_job_runs_with_safe_summary(tmp_path) -> None:
    runner = JobRunner(_database_url(tmp_path))

    result = runner.run_job("smoke_test")

    assert result.status == "success"
    assert result.summary["status"] == "ok"
    assert result.summary["steps"]["api"]["checked_endpoints"] >= 7

    rendered = json.dumps(result.to_dict(), ensure_ascii=False).lower()
    for marker in SENSITIVE_MARKERS:
        assert marker.lower() not in rendered
