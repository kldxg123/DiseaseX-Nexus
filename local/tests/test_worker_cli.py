from __future__ import annotations

import json

from worker_agent.cli import main


SENSITIVE_MARKERS = ["服务器信息.txt", "secret", "token", "password", "private key"]


def _database_url(tmp_path) -> str:
    return f"sqlite:///{(tmp_path / 'worker-cli.db').as_posix()}"


def _assert_safe_output(output: str) -> None:
    rendered = output.lower()
    for marker in SENSITIVE_MARKERS:
        assert marker.lower() not in rendered


def test_worker_cli_list_outputs_json(capsys) -> None:
    result = main(["list"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert result == 0
    assert any(job["name"] == "seed_sources" for job in payload["jobs"])
    _assert_safe_output(captured.out)


def test_worker_cli_list_tasks_outputs_json(capsys) -> None:
    result = main(["list-tasks"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert result == 0
    assert any(task["name"] == "full_local_pipeline" for task in payload["tasks"])
    _assert_safe_output(captured.out)


def test_worker_cli_describe_schedule_outputs_json(capsys) -> None:
    result = main(["describe-schedule"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert result == 0
    assert any(entry["name"] == "daily_clean" for entry in payload["schedule"])
    assert payload["warnings"] == []
    _assert_safe_output(captured.out)


def test_worker_cli_run_seed_sources_success(tmp_path, capsys) -> None:
    result = main(["run", "--job", "seed_sources", "--database-url", _database_url(tmp_path)])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert result == 0
    assert payload["result"]["status"] == "success"
    assert payload["result"]["summary"]["sources_count"] > 0
    _assert_safe_output(captured.out)


def test_worker_cli_run_task_seed_sources_success(tmp_path, capsys) -> None:
    result = main(["run-task", "--task", "seed_sources", "--database-url", _database_url(tmp_path)])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert result == 0
    assert payload["result"]["status"] == "success"
    assert payload["result"]["job"]["summary"]["sources_count"] > 0
    _assert_safe_output(captured.out)


def test_worker_cli_run_task_missing_mock_input_returns_nonzero(tmp_path, capsys) -> None:
    result = main(["run-task", "--task", "mock_crawl_url", "--database-url", _database_url(tmp_path)])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert result != 0
    assert payload["result"]["status"] == "failed"
    _assert_safe_output(captured.out)


def test_worker_cli_run_pipeline_full_local_pipeline_success(tmp_path, capsys) -> None:
    result = main(
        [
            "run-pipeline",
            "--pipeline",
            "full_local_pipeline",
            "--database-url",
            _database_url(tmp_path),
            "--country",
            "United States",
            "--disease",
            "norovirus",
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert result == 0
    assert payload["summary"]["status"] in {"success", "partial"}
    assert payload["summary"]["sources"] > 0
    assert payload["summary"]["features"] > 0
    _assert_safe_output(captured.out)


def test_worker_cli_unknown_job_returns_nonzero(tmp_path, capsys) -> None:
    result = main(["run", "--job", "missing_job", "--database-url", _database_url(tmp_path)])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert result != 0
    assert payload["result"]["status"] == "failed"
    _assert_safe_output(captured.out)


def test_worker_cli_invalid_params_json_returns_nonzero(tmp_path, capsys) -> None:
    result = main(
        [
            "run",
            "--job",
            "seed_sources",
            "--database-url",
            _database_url(tmp_path),
            "--params-json",
            "{invalid",
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert result != 0
    assert payload["status"] == "failed"
    _assert_safe_output(captured.out)


def test_worker_cli_run_many_outputs_json(tmp_path, capsys) -> None:
    jobs_json = json.dumps([{"name": "seed_sources"}, {"name": "clean_pending", "params": {"limit": 5}}])

    result = main(["run-many", "--database-url", _database_url(tmp_path), "--jobs-json", jobs_json])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert result == 0
    assert len(payload["results"]) == 2
    assert payload["results"][0]["status"] == "success"
    _assert_safe_output(captured.out)
