from __future__ import annotations

from common.db import get_session, init_db
from tests.deepseek_audit_fixture import create_deepseek_audit_fixture
from worker_agent import TaskRunner


def test_worker_runs_deepseek_extraction_quality_audit(tmp_path) -> None:
    report_dir, database_url, _run_id = create_deepseek_audit_fixture(tmp_path)
    worker_url = f"sqlite:///{(tmp_path / 'worker.sqlite').as_posix()}"
    init_db(worker_url).dispose()

    with get_session(worker_url) as session:
        result = TaskRunner(session).run_task(
            "deepseek_extraction_quality_audit",
            report_dir=str(report_dir),
            database_url=database_url,
        )

    assert result["status"] == "success"
    assert result["job"]["summary"]["records_extracted"] == 3
    assert result["job"]["summary"]["network_accessed"] is False

