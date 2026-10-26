from __future__ import annotations

from common.db import get_session, init_db
from worker_agent.tasks import TaskRunner, list_task_names


def test_worker_goal_mode_task_registered_and_runs_mock(tmp_path) -> None:
    assert "deepseek_table_review_goal_mode" in list_task_names()

    database_url = f"sqlite:///{(tmp_path / 'worker-goal-mode.sqlite').as_posix()}"
    init_db(database_url).dispose()
    with get_session(database_url) as session:
        result = TaskRunner(session).run_task(
            "deepseek_table_review_goal_mode",
            targets="dengue:Global",
            date_start="2026-05-01",
            date_end="2026-07-01",
            output_dir=str(tmp_path / "goal"),
            mock_search=True,
            mock_deepseek=True,
        )

    assert result["status"] == "success"
    summary = result["job"]["summary"]
    assert summary["attempts"][0]["review_rows_generated"] >= 1
    assert summary["network_accessed"] is False
