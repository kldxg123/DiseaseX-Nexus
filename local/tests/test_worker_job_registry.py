from __future__ import annotations

import json

import pytest

from worker_agent import InMemoryJobRegistry, safe_job_summary, get_job_definition, get_job_definitions


def test_worker_registry_contains_default_jobs() -> None:
    definitions = get_job_definitions()

    assert {
        "seed_sources",
        "mock_source_discovery",
        "clean_pending",
        "build_features",
        "backtest_model",
        "predict_latest",
        "minimum_data_analysis",
        "smoke_test",
    }.issubset(definitions)
    assert all(definition.allows_network is False for definition in definitions.values())
    assert "mock GDELT fixture" in definitions["mock_source_discovery"].description


def test_unknown_worker_job_raises_value_error() -> None:
    with pytest.raises(ValueError):
        get_job_definition("missing_job")


def test_in_memory_job_registry_lifecycle_and_safe_summary() -> None:
    registry = InMemoryJobRegistry()
    job = registry.create_job("seed_sources")

    assert job.status == "pending"
    assert registry.mark_running(job.job_id).status == "running"
    succeeded = registry.mark_success(
        job.job_id,
        summary={"created": 1, "token": "do-not-show"},
        warnings=["safe warning"],
    )
    payload = safe_job_summary(succeeded)
    rendered = json.dumps(payload, ensure_ascii=False).lower()

    assert payload["status"] == "success"
    assert len(registry.list_jobs()) == 1
    assert "do-not-show" not in rendered
    assert "token" not in rendered
    assert "password" not in rendered
    assert "secret" not in rendered


def test_in_memory_job_registry_failed_summary_is_sanitized() -> None:
    registry = InMemoryJobRegistry()
    job = registry.create_job("mock_crawl_url")
    failed = registry.mark_failed(job.job_id, "ValueError: password token secret key should not leak")

    payload = safe_job_summary(failed)
    rendered = json.dumps(payload, ensure_ascii=False).lower()

    assert payload["status"] == "failed"
    assert "password" not in rendered
    assert "token" not in rendered
    assert "secret" not in rendered
    assert " key " not in rendered
