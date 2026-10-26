from __future__ import annotations

from common.db import get_session, init_db
from tests.deepseek_human_review_fixture import create_human_review_fixture
from worker_agent import TaskRunner
from worker_agent.scheduler import validate_schedule


def test_human_review_worker_tasks_are_registered_and_run_without_network(tmp_path) -> None:
    report_dir, _database_url, output_dir = create_human_review_fixture(tmp_path)
    worker_url = f"sqlite:///{(tmp_path / 'worker.sqlite').as_posix()}"
    init_db(worker_url).dispose()
    with get_session(worker_url) as session:
        runner = TaskRunner(session)
        export = runner.run_task(
            "deepseek_human_review_template_export",
            report_dir=str(report_dir),
            output_dir=str(output_dir),
        )
        imported = runner.run_task(
            "deepseek_event_promotion_gate",
            report_dir=str(report_dir),
            annotation_file=str(output_dir / "deepseek_human_review_template.json"),
            output_dir=str(output_dir),
            allow_persistence=False,
        )

    assert export["status"] == "success"
    assert export["job"]["summary"]["network_accessed"] is False
    assert imported["status"] == "success"
    assert imported["job"]["summary"]["promoted_epidemic_events"] == 0
    assert validate_schedule(
        [
            {
                "name": "manual_deepseek_event_promotion_gate",
                "task_name": "deepseek_event_promotion_gate",
                "cadence": "manual",
                "enabled": False,
                "description": "test",
            }
        ]
    ) == []

