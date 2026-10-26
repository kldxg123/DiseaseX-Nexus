from __future__ import annotations

from common.db import get_session
from worker_agent.job_registry import get_job_definitions
from worker_agent.tasks import TaskRunner

from acquisition_quality_helpers import make_acquisition_db, make_run_dir


def test_acquisition_quality_worker_task_registered_and_runs(tmp_path):
    database_url = make_acquisition_db(tmp_path, weeks=1)
    run_dir = make_run_dir(tmp_path)

    definitions = get_job_definitions()
    assert "candidate_acquisition_quality_diagnose" in definitions
    assert definitions["candidate_acquisition_quality_diagnose"].allows_network is False

    with get_session(database_url) as session:
        result = TaskRunner(session).run_task(
            "candidate_acquisition_quality_diagnose",
            run_dir=str(run_dir),
            database_url=database_url,
        )

    assert result["status"] == "success"
    summary = result["job"]["summary"]
    assert summary["model_features_count"] == 0
    assert summary["event_quality_status"] == "insufficient_temporal_signal"


def test_acquisition_quality_worker_requires_database_url(tmp_path):
    database_url = make_acquisition_db(tmp_path, weeks=1)
    with get_session(database_url) as session:
        result = TaskRunner(session).run_task("candidate_acquisition_quality_diagnose")

    assert result["status"] == "failed"
