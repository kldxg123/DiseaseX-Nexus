from __future__ import annotations

from fastapi import APIRouter, Depends

from apps.api.deps import get_database_url
from apps.api.schemas import JobRunRequest
from worker_agent.job_registry import get_job_definitions
from worker_agent.job_runner import JobRunner
from worker_agent.scheduler import describe_schedule, get_default_schedule, validate_schedule
from worker_agent.job_types import sanitize_for_json

router = APIRouter(tags=["jobs"])


@router.get("/jobs")
def list_jobs() -> dict:
    return {
        "jobs": [
            {
                "name": definition.name,
                "description": definition.description,
                "safe_for_scheduler": definition.safe_for_scheduler,
                "allows_network": definition.allows_network,
            }
            for definition in get_job_definitions().values()
        ]
    }


@router.get("/jobs/schedule")
def jobs_schedule() -> dict:
    schedule = get_default_schedule()
    return sanitize_for_json(
        {
            "schedule": describe_schedule(schedule),
            "warnings": validate_schedule(schedule),
        }
    )


@router.post("/jobs/run")
def run_job(request: JobRunRequest, database_url: str = Depends(get_database_url)) -> dict:
    result = JobRunner(database_url).run_job(request.job_name, request.params)
    return sanitize_for_json({"result": result.to_dict()})
