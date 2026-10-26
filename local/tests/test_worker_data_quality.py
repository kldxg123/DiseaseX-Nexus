from __future__ import annotations

from common.db import get_session, init_db
from tests.test_data_quality_profile import _seed_quality_rows
from worker_agent.job_registry import get_job_definitions
from worker_agent.scheduler import describe_schedule, get_default_schedule, get_default_schedules
from worker_agent.tasks import TaskRunner, list_task_names


def test_worker_data_quality_task_writes_safe_report(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'worker-quality.db'}"
    init_db(database_url).dispose()
    with get_session(database_url) as session:
        _seed_quality_rows(session)
        result = TaskRunner(session).run_task("data_quality_report", output_dir=str(tmp_path / "reports"))

    assert result["status"] == "success"
    summary = result["job"]["summary"]
    assert summary["report_json"].endswith(".json")
    assert summary["report_markdown"].endswith(".md")
    assert "feature_json" not in str(summary)
    assert "lesson_json" not in str(summary)


def test_worker_registry_and_schedule_include_data_quality_report() -> None:
    assert "data_quality_report" in list_task_names()
    assert "data_quality_report" in get_job_definitions()
    assert any(entry.job_name == "data_quality_report" for entry in get_default_schedules())
    described = describe_schedule(get_default_schedule())
    assert any(item["name"] == "weekly_data_quality_report" for item in described)
