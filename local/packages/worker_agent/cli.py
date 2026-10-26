from __future__ import annotations

import argparse
import json
from typing import Any

from common.db import get_session, init_db
from worker_agent.job_registry import get_job_definitions
from worker_agent.job_runner import JobRunner
from worker_agent.job_types import JOB_STATUS_FAILED, sanitize_for_json
from worker_agent.pipelines import PipelineRunner
from worker_agent.scheduler import (
    SimpleScheduler,
    describe_schedule,
    get_default_schedule,
    get_default_schedules,
    validate_schedule,
)
from worker_agent.tasks import TaskRunner


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run local worker/scheduler MVP jobs.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list", help="List registered local jobs.")
    subparsers.add_parser("list-tasks", help="List registered local tasks.")
    subparsers.add_parser("describe-schedule", help="Describe default local schedule config.")

    run_parser = subparsers.add_parser("run", help="Run one job.")
    run_parser.add_argument("--job", required=True)
    run_parser.add_argument("--database-url", default=None)
    run_parser.add_argument("--params-json", default="{}")

    many_parser = subparsers.add_parser("run-many", help="Run a list of jobs.")
    many_parser.add_argument("--database-url", default=None)
    many_parser.add_argument("--jobs-json", required=True)

    due_parser = subparsers.add_parser("run-due-once", help="Run currently due default schedules once.")
    due_parser.add_argument("--database-url", default=None)

    loop_parser = subparsers.add_parser("loop", help="Run the local scheduler loop.")
    loop_parser.add_argument("--database-url", default=None)
    loop_parser.add_argument("--poll-seconds", type=float, default=5.0)
    loop_parser.add_argument("--max-iterations", type=int, default=None)

    task_parser = subparsers.add_parser("run-task", help="Run one local TaskRunner task.")
    task_parser.add_argument("--database-url", default=None)
    task_parser.add_argument("--task", required=True)
    task_parser.add_argument("--params-json", default="{}")
    task_parser.add_argument("--country", default=None)
    task_parser.add_argument("--disease", default=None)
    task_parser.add_argument("--limit-sources", type=int, default=None)
    task_parser.add_argument("--limit-documents", type=int, default=None)
    task_parser.add_argument("--policy-path", default=None)
    task_parser.add_argument("--fixture", default=None)
    task_parser.add_argument("--output-dir", default=None)
    task_parser.add_argument("--min-source-score", type=float, default=None)
    task_parser.add_argument("--parser-strategy", default=None)
    task_parser.add_argument("--no-include-connector-specs", action="store_true")
    task_parser.add_argument("--no-connector-specs", action="store_true")
    task_parser.add_argument("--apply-safe-actions", action="store_true")

    pipeline_parser = subparsers.add_parser("run-pipeline", help="Run one local pipeline.")
    pipeline_parser.add_argument("--database-url", default=None)
    pipeline_parser.add_argument("--pipeline", required=True)
    pipeline_parser.add_argument("--country", default="United States")
    pipeline_parser.add_argument("--disease", default="norovirus")
    pipeline_parser.add_argument("--horizon-days", type=int, default=28)

    args = parser.parse_args(argv)
    try:
        if args.command == "list":
            return _print_json(_list_jobs())
        if args.command == "list-tasks":
            return _print_json(_list_tasks())
        if args.command == "describe-schedule":
            schedule = get_default_schedule()
            return _print_json(
                {
                    "schedule": describe_schedule(schedule),
                    "warnings": validate_schedule(schedule),
                }
            )
        if args.command == "run":
            params = _loads_object(args.params_json, "params-json")
            result = JobRunner(args.database_url).run_job(args.job, params)
            _print_json({"result": result.to_dict()})
            return 0 if result.status != JOB_STATUS_FAILED else 1
        if args.command == "run-many":
            jobs = _loads_list(args.jobs_json, "jobs-json")
            results = JobRunner(args.database_url).run_many(jobs)
            _print_json({"results": [result.to_dict() for result in results]})
            return 0 if all(result.status != JOB_STATUS_FAILED for result in results) else 1
        if args.command == "run-due-once":
            runner = JobRunner(args.database_url)
            scheduler = SimpleScheduler(runner, get_default_schedules())
            results = scheduler.run_due_once()
            _print_json({"results": [result.to_dict() for result in results]})
            return 0 if all(result.status != JOB_STATUS_FAILED for result in results) else 1
        if args.command == "loop":
            runner = JobRunner(args.database_url)
            scheduler = SimpleScheduler(runner, get_default_schedules())
            results = scheduler.run_loop(
                poll_seconds=args.poll_seconds,
                max_iterations=args.max_iterations,
            )
            _print_json({"results": [result.to_dict() for result in results]})
            return 0 if all(result.status != JOB_STATUS_FAILED for result in results) else 1
        if args.command == "run-task":
            params = _loads_object(args.params_json, "params-json")
            params.update(_task_cli_params(args))
            init_db(args.database_url).dispose()
            with get_session(args.database_url) as session:
                result = TaskRunner(session).run_task(args.task, **params)
            _print_json({"result": result})
            return 0 if result.get("status") != "failed" else 1
        if args.command == "run-pipeline":
            if args.pipeline != "full_local_pipeline":
                _print_json({"status": "failed", "error": "unknown pipeline"})
                return 2
            init_db(args.database_url).dispose()
            with get_session(args.database_url) as session:
                summary = PipelineRunner(TaskRunner(session)).run_full_local_pipeline(
                    country=args.country,
                    disease=args.disease,
                    horizon_days=args.horizon_days,
                )
            _print_json({"pipeline": args.pipeline, "summary": summary})
            return 0 if summary.get("status") != "failed" else 1
    except ValueError as exc:
        _print_json({"status": "failed", "error": sanitize_for_json(str(exc))})
        return 2
    _print_json({"status": "failed", "error": "unknown command"})
    return 2


def _list_jobs() -> dict[str, Any]:
    definitions = get_job_definitions()
    return {
        "jobs": [
            {
                "name": definition.name,
                "description": definition.description,
                "safe_for_scheduler": definition.safe_for_scheduler,
                "allows_network": definition.allows_network,
                "default_params": definition.default_params,
            }
            for definition in definitions.values()
        ]
    }


def _list_tasks() -> dict[str, Any]:
    return {
        "tasks": [
            {
                "name": definition.name,
                "description": definition.description,
                "safe_for_scheduler": definition.safe_for_scheduler,
                "allows_network": definition.allows_network,
                "default_params": definition.default_params,
            }
            for definition in get_job_definitions().values()
        ]
    }


def _loads_object(value: str, label: str) -> dict:
    try:
        payload = json.loads(value or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid {label}: JSON object required.") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Invalid {label}: JSON object required.")
    return payload


def _loads_list(value: str, label: str) -> list[dict]:
    try:
        payload = json.loads(value or "[]")
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid {label}: JSON list required.") from exc
    if not isinstance(payload, list):
        raise ValueError(f"Invalid {label}: JSON list required.")
    return payload


def _task_cli_params(args) -> dict[str, Any]:
    params: dict[str, Any] = {}
    for name in (
        "country",
        "disease",
        "limit_sources",
        "limit_documents",
        "policy_path",
        "fixture",
        "output_dir",
        "min_source_score",
        "parser_strategy",
    ):
        value = getattr(args, name, None)
        if value is not None:
            params[name] = value
    if getattr(args, "no_include_connector_specs", False):
        params["include_connector_specs"] = False
    if getattr(args, "apply_safe_actions", False):
        params["apply_safe_actions"] = True
    if getattr(args, "no_connector_specs", False):
        params["generate_connector_specs"] = False
    return params


def _print_json(payload: dict[str, Any]) -> int:
    print(json.dumps(sanitize_for_json(payload), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
