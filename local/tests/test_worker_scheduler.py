from __future__ import annotations

from datetime import UTC, datetime, timedelta

from worker_agent import (
    JobRunner,
    ScheduleEntry,
    SimpleScheduler,
    describe_schedule,
    get_default_schedule,
    get_default_schedules,
    validate_schedule,
)


def _database_url(tmp_path) -> str:
    return f"sqlite:///{(tmp_path / 'worker-scheduler.db').as_posix()}"


def test_default_schedules_keep_network_and_manual_jobs_disabled() -> None:
    schedules = {entry.job_name: entry for entry in get_default_schedules()}

    assert schedules["mock_source_discovery"].enabled is False
    assert schedules["smoke_test"].enabled is False
    assert schedules["seed_sources"].every_seconds == 86400


def test_default_schedule_description_and_validation() -> None:
    schedule = get_default_schedule()
    descriptions = describe_schedule(schedule)
    by_name = {entry["name"]: entry for entry in descriptions}

    assert validate_schedule(schedule) == []
    assert "daily_clean" in by_name
    assert "daily_feature_build" in by_name
    assert "daily_predict" in by_name
    assert "weekly_minimum_data_analysis" in by_name
    assert by_name["hourly_mock_crawl_disabled_by_default"]["enabled"] is False
    assert by_name["daily_source_discovery_mock"]["enabled"] is False


def test_schedule_validation_flags_unknown_task() -> None:
    errors = validate_schedule(
        [
            {
                "name": "bad",
                "task_name": "missing_task",
                "cadence": "daily",
                "enabled": True,
                "description": "bad",
            }
        ]
    )

    assert errors
    assert "unknown task_name" in errors[0]


def test_scheduler_detects_due_jobs_and_runs_once(tmp_path) -> None:
    runner = JobRunner(_database_url(tmp_path))
    scheduler = SimpleScheduler(
        runner,
        [
            ScheduleEntry("seed_sources", every_seconds=60, params={}),
            ScheduleEntry("smoke_test", every_seconds=60, params={}, enabled=True),
        ],
    )
    now = datetime(2026, 1, 1, tzinfo=UTC)

    due = scheduler.due_jobs(now)
    results = scheduler.run_due_once(now)

    assert [entry.job_name for entry in due] == ["seed_sources"]
    assert len(results) == 1
    assert results[0].status == "success"
    assert scheduler.due_jobs(now) == []
    assert scheduler.due_jobs(now + timedelta(seconds=61))[0].job_name == "seed_sources"


def test_scheduler_run_loop_exits_with_max_iterations(tmp_path) -> None:
    runner = JobRunner(_database_url(tmp_path))
    scheduler = SimpleScheduler(runner, [ScheduleEntry("seed_sources", every_seconds=60, params={})])

    results = scheduler.run_loop(poll_seconds=0.0, max_iterations=1)

    assert len(results) == 1
    assert results[0].status == "success"
