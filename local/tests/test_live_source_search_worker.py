from __future__ import annotations

from common.db import get_session, init_db
from worker_agent.job_registry import get_job_definitions
from worker_agent.tasks import TaskRunner


def test_worker_live_source_search_tasks_registered_and_dry_run(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'worker-live-search.db'}"
    init_db(database_url).dispose()
    definitions = get_job_definitions()
    assert "live_source_search_dry_run" in definitions
    assert "live_source_search_controlled" in definitions
    assert definitions["live_source_search_controlled"].allows_network is True
    with get_session(database_url) as session:
        result = TaskRunner(session).run_task(
            "live_source_search_dry_run",
            country="United States",
            disease="cholera",
            output_dir=str(tmp_path / "reports"),
        )
    assert result["status"] == "success"
    assert result["job"]["summary"]["request_count_total"] == 0
    assert result["job"]["summary"]["candidate_urls_visited"] is False
