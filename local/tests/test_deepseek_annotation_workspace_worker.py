from __future__ import annotations

from common.db import get_session, init_db
from tests.deepseek_human_review_fixture import create_human_review_fixture
from worker_agent import TaskRunner
from worker_agent.scheduler import validate_schedule


def test_annotation_workspace_worker_tasks_are_registered_and_run(tmp_path) -> None:
    report_dir, _database_url, output_dir = create_human_review_fixture(tmp_path)
    worker_url = f"sqlite:///{(tmp_path / 'worker.sqlite').as_posix()}"
    init_db(worker_url).dispose()

    with get_session(worker_url) as session:
        runner = TaskRunner(session)
        export = runner.run_task(
            "deepseek_annotation_workspace_export",
            report_dir=str(report_dir),
            output_dir=str(output_dir / "workspace"),
        )
        validate = runner.run_task(
            "deepseek_annotation_draft_validate",
            annotation_file=str(output_dir / "workspace" / "annotation_draft_template.json"),
        )
        preview = runner.run_task(
            "deepseek_annotation_promotion_preview",
            annotation_file=str(output_dir / "workspace" / "annotation_draft_template.json"),
        )

    assert export["status"] == "success"
    assert export["job"]["summary"]["network_accessed"] is False
    assert validate["status"] == "success"
    assert preview["status"] == "success"
    assert validate_schedule(
        [
            {
                "name": "manual_deepseek_annotation_workspace_export",
                "task_name": "deepseek_annotation_workspace_export",
                "cadence": "manual",
                "enabled": False,
                "description": "test",
            }
        ]
    ) == []

