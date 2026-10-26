from __future__ import annotations

import json

from common.db import get_session, init_db
from worker_agent import JobRunner, TaskRunner, get_default_schedule, get_default_schedules
from worker_agent.cli import main


FORBIDDEN = (
    "服务器信息.txt",
    "ssh_key_path",
    "password",
    "token",
    "secret",
    "private key",
)


def _database_url(tmp_path) -> str:
    return f"sqlite:///{(tmp_path / 'worker-access-policy.db').as_posix()}"


def test_task_runner_check_access_policies(tmp_path) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()
    with get_session(database_url) as session:
        result = TaskRunner(session).run_task("check_access_policies")

    assert result["status"] == "success"
    summary = result["job"]["summary"]
    assert summary["policies_checked"] == 6
    assert summary["errors_count"] == 0
    assert summary["guardrail_status"] == "ok"
    assert summary["network_accessed"] is False
    _assert_safe(result)


def test_job_runner_check_access_policies(tmp_path) -> None:
    result = JobRunner(_database_url(tmp_path)).run_job("check_access_policies")

    assert result.status == "success"
    assert result.summary["policies_checked"] == 6
    assert result.summary["errors_count"] == 0
    _assert_safe(result.to_dict())


def test_worker_cli_run_task_check_access_policies(tmp_path, capsys) -> None:
    result = main(
        [
            "run-task",
            "--database-url",
            _database_url(tmp_path),
            "--task",
            "check_access_policies",
            "--policy-path",
            "configs/source_access_policy.example.json",
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert result == 0
    assert payload["result"]["status"] == "success"
    assert payload["result"]["job"]["summary"]["policies_checked"] == 6
    _assert_safe(payload)


def test_worker_schedule_contains_daily_access_policy_check() -> None:
    entries = {entry.job_name: entry for entry in get_default_schedules()}
    described = {entry["name"]: entry for entry in get_default_schedule()}

    assert entries["check_access_policies"].enabled is True
    assert described["daily_access_policy_check"]["task_name"] == "check_access_policies"
    assert described["daily_access_policy_check"]["enabled"] is True
    assert "no network request" in described["daily_access_policy_check"]["description"]


def _assert_safe(payload: dict) -> None:
    rendered = json.dumps(payload, ensure_ascii=False).lower()
    for marker in FORBIDDEN:
        assert marker.lower() not in rendered
