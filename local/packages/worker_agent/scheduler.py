from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

from worker_agent.job_registry import get_job_definition
from worker_agent.job_runner import JobRunner
from worker_agent.job_types import JobRunResult, sanitize_for_json
from worker_agent.tasks import list_task_names


@dataclass
class ScheduleEntry:
    job_name: str
    every_seconds: int
    params: dict = field(default_factory=dict)
    enabled: bool = True


class SimpleScheduler:
    def __init__(self, runner: JobRunner, schedules: list[ScheduleEntry]):
        self.runner = runner
        self.schedules = schedules
        self.last_run_at: dict[str, datetime] = {}

    def due_jobs(self, now=None) -> list[ScheduleEntry]:
        current = _as_datetime(now)
        due: list[ScheduleEntry] = []
        for entry in self.schedules:
            if not entry.enabled:
                continue
            definition = get_job_definition(entry.job_name)
            if not definition.safe_for_scheduler or definition.allows_network:
                continue
            last_run = self.last_run_at.get(_schedule_key(entry))
            if last_run is None:
                due.append(entry)
                continue
            elapsed = (current - last_run).total_seconds()
            if elapsed >= max(1, int(entry.every_seconds)):
                due.append(entry)
        return due

    def run_due_once(self, now=None) -> list[JobRunResult]:
        current = _as_datetime(now)
        results: list[JobRunResult] = []
        for entry in self.due_jobs(current):
            result = self.runner.run_job(entry.job_name, entry.params)
            results.append(result)
            self.last_run_at[_schedule_key(entry)] = current
        return results

    def run_loop(
        self,
        *,
        poll_seconds: float = 5.0,
        max_iterations: int | None = None,
    ) -> list[JobRunResult]:
        results: list[JobRunResult] = []
        iterations = 0
        while max_iterations is None or iterations < max_iterations:
            results.extend(self.run_due_once())
            iterations += 1
            if max_iterations is not None and iterations >= max_iterations:
                break
            time.sleep(max(0.0, float(poll_seconds)))
        return results


def get_default_schedules() -> list[ScheduleEntry]:
    return [
        ScheduleEntry("seed_sources", 86400, {}),
        ScheduleEntry("clean_pending", 3600, {"limit": 100}),
        ScheduleEntry("build_features", 86400, {}),
        ScheduleEntry("backtest_model", 604800, {}),
        ScheduleEntry("predict_latest", 86400, {}),
        ScheduleEntry("minimum_data_analysis", 604800, {}),
        ScheduleEntry("optimize_crawlers", 86400, {}),
        ScheduleEntry("check_access_policies", 86400, {}),
        ScheduleEntry("data_quality_report", 604800, {"output_dir": "reports"}),
        ScheduleEntry("prepare_live_data_pilot", 604800, {}, enabled=False),
        ScheduleEntry("mock_source_discovery", 86400, {}, enabled=False),
        ScheduleEntry("discover_sources_fixture", 86400, {}, enabled=False),
        ScheduleEntry("source_discovery_bridge_fixture", 86400, {}, enabled=False),
        ScheduleEntry("live_source_search_dry_run", 86400, {}, enabled=False),
        ScheduleEntry("deepseek_readiness_review_build", 86400, {}, enabled=False),
        ScheduleEntry("deepseek_manual_review_queue_export", 86400, {}, enabled=False),
        ScheduleEntry("deepseek_human_review_template_export", 86400, {}, enabled=False),
        ScheduleEntry("deepseek_human_review_annotations_import", 86400, {}, enabled=False),
        ScheduleEntry("deepseek_event_promotion_gate", 86400, {}, enabled=False),
        ScheduleEntry("deepseek_annotation_workspace_export", 86400, {}, enabled=False),
        ScheduleEntry("deepseek_annotation_draft_validate", 86400, {}, enabled=False),
        ScheduleEntry("deepseek_annotation_promotion_preview", 86400, {}, enabled=False),
        ScheduleEntry("smoke_test", 86400, {}, enabled=False),
    ]


def get_default_schedule() -> list[dict]:
    return [
        {
            "name": "daily_source_discovery_mock",
            "task_name": "discover_gdelt_mock",
            "cadence": "daily",
            "enabled": False,
            "description": "Mock GDELT source discovery only when a mock payload is supplied.",
        },
        {
            "name": "hourly_mock_crawl_disabled_by_default",
            "task_name": "mock_crawl_url",
            "cadence": "hourly",
            "enabled": False,
            "description": "Caller-provided HTML fixture ingestion; disabled by default.",
        },
        {
            "name": "daily_clean",
            "task_name": "clean_pending",
            "cadence": "daily",
            "enabled": True,
            "description": "Clean pending local raw documents.",
        },
        {
            "name": "daily_feature_build",
            "task_name": "build_features",
            "cadence": "daily",
            "enabled": True,
            "description": "Build local model features from cleaned epidemic events.",
        },
        {
            "name": "weekly_backtest",
            "task_name": "backtest_model",
            "cadence": "weekly",
            "enabled": True,
            "description": "Run offline baseline backtest.",
        },
        {
            "name": "daily_predict",
            "task_name": "predict_latest",
            "cadence": "daily",
            "enabled": True,
            "description": "Write latest local baseline prediction rows.",
        },
        {
            "name": "weekly_minimum_data_analysis",
            "task_name": "minimum_data_analysis",
            "cadence": "weekly",
            "enabled": True,
            "description": "Run offline minimum data analysis.",
        },
        {
            "name": "daily_crawler_optimization",
            "task_name": "optimize_crawlers",
            "cadence": "daily",
            "enabled": True,
            "description": (
                "Run local crawler failure diagnostics only; it does not fetch from the network, "
                "rewrite crawlers, or activate candidate sources."
            ),
        },
        {
            "name": "daily_access_policy_check",
            "task_name": "check_access_policies",
            "cadence": "daily",
            "enabled": True,
            "description": "Validate local source access policies only; no network request is made.",
        },
        {
            "name": "weekly_data_quality_report",
            "task_name": "data_quality_report",
            "cadence": "weekly",
            "enabled": True,
            "description": "Build local data-quality and label-audit report files.",
        },
        {
            "name": "manual_prepare_live_data_pilot",
            "task_name": "prepare_live_data_pilot",
            "cadence": "manual",
            "enabled": False,
            "description": "Prepare a CDC NoroSTAT live pilot plan only; it does not execute live fetch.",
        },
        {
            "name": "manual_source_discovery_fixture",
            "task_name": "discover_sources_fixture",
            "cadence": "manual",
            "enabled": False,
            "description": "Discover candidate sources from a local fixture only; no live search is executed.",
        },
        {
            "name": "manual_source_discovery_bridge_fixture",
            "task_name": "source_discovery_bridge_fixture",
            "cadence": "manual",
            "enabled": False,
            "description": "Bridge local source-discovery fixture results to generic parsers; no live fetch is executed.",
        },
        {
            "name": "manual_live_source_search_dry_run",
            "task_name": "live_source_search_dry_run",
            "cadence": "manual",
            "enabled": False,
            "description": "Plan provider-only source search and report review queue without network access.",
        },
        {
            "name": "manual_live_source_search_controlled",
            "task_name": "live_source_search_controlled",
            "cadence": "manual",
            "enabled": False,
            "description": "Controlled live provider-only source search; requires explicit network and review flags.",
        },
        {
            "name": "manual_live_source_metadata_check",
            "task_name": "live_source_metadata_check",
            "cadence": "manual",
            "enabled": False,
            "description": "Controlled metadata check over refined top candidates; requires explicit network and review flags.",
        },
        {
            "name": "manual_deepseek_readiness_review_build",
            "task_name": "deepseek_readiness_review_build",
            "cadence": "manual",
            "enabled": False,
            "description": "Build local DeepSeek evidence readiness review table; no network request is made.",
        },
        {
            "name": "manual_deepseek_manual_review_queue_export",
            "task_name": "deepseek_manual_review_queue_export",
            "cadence": "manual",
            "enabled": False,
            "description": "Export local DeepSeek manual review queue; no network request is made.",
        },
        {
            "name": "manual_deepseek_human_review_template_export",
            "task_name": "deepseek_human_review_template_export",
            "cadence": "manual",
            "enabled": False,
            "description": "Export a local DeepSeek annotation template; no network request is made.",
        },
        {
            "name": "manual_deepseek_human_review_annotations_import",
            "task_name": "deepseek_human_review_annotations_import",
            "cadence": "manual",
            "enabled": False,
            "description": "Import and validate local DeepSeek human review annotations; persistence is disabled by default.",
        },
        {
            "name": "manual_deepseek_event_promotion_gate",
            "task_name": "deepseek_event_promotion_gate",
            "cadence": "manual",
            "enabled": False,
            "description": "Run local event promotion gate; writing events requires explicit allow_persistence.",
        },
        {
            "name": "manual_deepseek_annotation_workspace_export",
            "task_name": "deepseek_annotation_workspace_export",
            "cadence": "manual",
            "enabled": False,
            "description": "Export local DeepSeek annotation workspace bundle; no network request is made.",
        },
        {
            "name": "manual_deepseek_annotation_draft_validate",
            "task_name": "deepseek_annotation_draft_validate",
            "cadence": "manual",
            "enabled": False,
            "description": "Validate local DeepSeek annotation draft; no persistence is performed.",
        },
        {
            "name": "manual_deepseek_annotation_promotion_preview",
            "task_name": "deepseek_annotation_promotion_preview",
            "cadence": "manual",
            "enabled": False,
            "description": "Preview local DeepSeek promotion decisions without writing a database.",
        },
    ]


def validate_schedule(schedule: list[dict]) -> list[str]:
    errors: list[str] = []
    allowed_tasks = set(list_task_names())
    if not isinstance(schedule, list):
        return ["schedule must be a list"]
    for index, entry in enumerate(schedule):
        if not isinstance(entry, dict):
            errors.append(f"schedule[{index}] must be an object")
            continue
        for key in ("name", "task_name", "cadence", "enabled", "description"):
            if key not in entry:
                errors.append(f"schedule[{index}] missing {key}")
        task_name = str(entry.get("task_name") or "")
        if task_name and task_name not in allowed_tasks:
            errors.append(f"schedule[{index}] has unknown task_name")
        if "enabled" in entry and not isinstance(entry.get("enabled"), bool):
            errors.append(f"schedule[{index}] enabled must be boolean")
    return errors


def describe_schedule(schedule: list[dict]) -> list[dict]:
    descriptions: list[dict] = []
    for entry in schedule:
        if not isinstance(entry, dict):
            continue
        descriptions.append(
            {
                "name": entry.get("name"),
                "task_name": entry.get("task_name"),
                "cadence": entry.get("cadence"),
                "enabled": bool(entry.get("enabled", False)),
                "description": entry.get("description"),
            }
        )
    return sanitize_for_json(descriptions)


def _schedule_key(entry: ScheduleEntry) -> str:
    return f"{entry.job_name}:{sorted((entry.params or {}).items())}"


def _as_datetime(value) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value
    raise TypeError("now must be datetime or None.")
