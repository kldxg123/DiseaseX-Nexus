from __future__ import annotations

from worker_agent.job_registry import get_job_definitions
from worker_agent.tasks import list_task_names


def test_live_source_metadata_check_worker_task_is_registered():
    assert "live_source_metadata_check" in list_task_names()
    definition = get_job_definitions()["live_source_metadata_check"]
    assert definition.allows_network is True
    assert definition.safe_for_scheduler is False
