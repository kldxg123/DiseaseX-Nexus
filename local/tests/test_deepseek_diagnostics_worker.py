from __future__ import annotations

from common.db import get_session, init_db
from tests.test_deepseek_structured_extraction_replay import make_safe_external_run_dir
from worker_agent import TaskRunner


def test_worker_deepseek_diagnostics_tasks(tmp_path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'worker-deepseek.sqlite').as_posix()}"
    init_db(database_url).dispose()
    run_dir = make_safe_external_run_dir(tmp_path)

    with get_session(database_url) as session:
        runner = TaskRunner(session)
        diagnostic = runner.run_task("deepseek_api_diagnose", allow_deepseek_api=False)
        replay = runner.run_task(
            "deepseek_extraction_replay",
            run_dir=str(run_dir),
            allow_deepseek_api=False,
            output_dir=str(tmp_path / "reports"),
        )

    assert diagnostic["status"] == "success"
    assert diagnostic["job"]["summary"]["network_accessed"] is False
    assert replay["status"] == "success"
    assert replay["job"]["summary"]["no_tavily_rerun"] is True
    assert replay["job"]["summary"]["records_persisted"] == 1

