from __future__ import annotations

from common.db import get_session, init_db
from deepseek_web_search_agent.extraction_quality_audit import write_deepseek_quality_audit_report
from tests.deepseek_audit_fixture import create_deepseek_audit_fixture
from worker_agent import TaskRunner
from worker_agent.scheduler import validate_schedule


def test_worker_readiness_review_tasks_are_registered_and_run(tmp_path) -> None:
    report_dir, database_url, _run_id = create_deepseek_audit_fixture(tmp_path)
    write_deepseek_quality_audit_report(report_dir, database_url=database_url)
    worker_url = f"sqlite:///{(tmp_path / 'worker.sqlite').as_posix()}"
    init_db(worker_url).dispose()

    with get_session(worker_url) as session:
        runner = TaskRunner(session)
        build = runner.run_task("deepseek_readiness_review_build", report_dir=str(report_dir))
        export = runner.run_task("deepseek_manual_review_queue_export", report_dir=str(report_dir))

    assert build["status"] == "success"
    assert build["job"]["summary"]["total_records"] == 3
    assert build["job"]["summary"]["network_accessed"] is False
    assert export["status"] == "success"
    assert export["job"]["summary"]["review_queue_count"] == 3
    assert validate_schedule(
        [
            {
                "name": "manual_deepseek_readiness_review_build",
                "task_name": "deepseek_readiness_review_build",
                "cadence": "manual",
                "enabled": False,
                "description": "test",
            }
        ]
    ) == []

