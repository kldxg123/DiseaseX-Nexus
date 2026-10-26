from __future__ import annotations

import json

from scripts.smoke_test import run_smoke_test


SENSITIVE_MARKERS = [
    "服务器信息.txt",
    "secret",
    "token",
    "password",
    "private key",
]


def test_run_smoke_test_uses_temp_sqlite_and_runs_pipeline() -> None:
    summary = run_smoke_test()

    assert summary["status"] == "ok"
    assert summary["database"] == "sqlite"
    assert summary["database_scope"] == "temp"
    assert summary["created_sources_count"] > 0
    assert summary["created_raw_documents_count"] >= 20
    assert summary["created_events_count"] > 0
    assert summary["features_count"] > 0
    assert summary["predictions_count"] > 0
    assert summary["feedback_count"] == 1
    assert summary["experience_count"] == 1
    assert summary["minimum_data"]["minimum_feature_set"]
    assert summary["crawler_optimizer"]["status"] == "ok"
    assert summary["crawler_optimizer"]["recommended_action_count"] >= 0
    assert summary["access_policy"]["guardrail_status"] == "ok"
    assert summary["access_policy"]["policies_checked"] == 6
    assert summary["access_policy"]["errors_count"] == 0
    assert summary["source_discovery"]["status"] == "ok"
    assert summary["source_discovery"]["candidates_found"] > 0
    assert summary["source_discovery"]["candidates_inserted_or_updated"] > 0
    assert summary["source_discovery"]["network_accessed"] is False
    assert summary["api_smoke"] == {}
    assert summary["worker_smoke"] == {}
    assert summary["network_accessed"] is False
    assert summary["services_started"] is False
    assert summary["orm_or_alembic_changed"] is False
    assert summary["tests_passed"] is True

    rendered = json.dumps(summary, ensure_ascii=False).lower()
    for marker in SENSITIVE_MARKERS:
        assert marker.lower() not in rendered


def test_run_smoke_test_can_include_api_and_worker() -> None:
    summary = run_smoke_test(include_api=True, include_worker=True)

    assert summary["status"] == "ok"
    assert summary["api_smoke"]["checked_endpoints"] >= 12
    assert summary["worker_smoke"]["ok"] is True
    assert summary["worker_daemon_started"] is False
    assert summary["fastapi_service_started"] is False
