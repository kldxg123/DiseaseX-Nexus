from __future__ import annotations

import json

from common.db import get_session, init_db
from worker_agent.job_registry import get_job_definitions
from worker_agent.tasks import TaskRunner


def test_candidate_acquisition_worker_tasks_registered_and_plan_runs(tmp_path):
    run_dir = _write_run_dir(tmp_path)
    database_url = f"sqlite:///{tmp_path / 'worker.db'}"
    init_db(database_url).dispose()

    definitions = get_job_definitions()
    assert "candidate_acquisition_plan" in definitions
    assert "candidate_acquisition_controlled" in definitions
    assert definitions["candidate_acquisition_controlled"].allows_network is True

    with get_session(database_url) as session:
        result = TaskRunner(session).run_task(
            "candidate_acquisition_plan",
            run_dir=str(run_dir),
            output_dir=str(tmp_path / "acquisition"),
        )

    assert result["status"] == "success"
    assert result["job"]["summary"]["status"] == "ok"
    assert result["job"]["summary"]["network_accessed"] is False


def test_candidate_acquisition_worker_controlled_requires_flags(tmp_path):
    run_dir = _write_run_dir(tmp_path)
    database_url = f"sqlite:///{tmp_path / 'worker.db'}"
    init_db(database_url).dispose()

    with get_session(database_url) as session:
        result = TaskRunner(session).run_task(
            "candidate_acquisition_controlled",
            run_dir=str(run_dir),
            output_dir=str(tmp_path / "acquisition"),
        )

    assert result["status"] == "failed"


def _write_run_dir(tmp_path):
    run_dir = tmp_path / "search_measles"
    run_dir.mkdir()
    (run_dir / "refined_review_queue.json").write_text(
        json.dumps(
            {
                "review_queue": [
                    {
                        "candidate_id": "a",
                        "title": "Cases and Outbreaks",
                        "domain": "www.cdc.gov",
                        "quality_gate_status": "pass",
                        "metadata_check_status": "data_source_candidate",
                        "data_source_likelihood": 0.68,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "metadata_check_results.json").write_text(
        json.dumps(
            {
                "results": [
                    {
                        "url": "https://www.cdc.gov/measles/data-research/index.html",
                        "domain": "www.cdc.gov",
                        "title_summary": "Measles Cases and Outbreaks | CDC",
                        "status": "data_source_candidate",
                        "data_source_likelihood": 0.68,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "candidate_quality_gate.json").write_text('{"candidate_quality_gate":[]}', encoding="utf-8")
    (run_dir / "live_source_search_summary.json").write_text('{"country":"United States","disease":"measles"}', encoding="utf-8")
    return run_dir
