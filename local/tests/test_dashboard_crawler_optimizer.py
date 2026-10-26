from __future__ import annotations

import json

import httpx

from apps.dashboard.api_client import DashboardApiClient
from apps.dashboard.formatters import (
    compact_optimizer_summary,
    rows_from_connector_specs,
    rows_from_optimizer_actions,
    rows_from_optimizer_failure_cases,
)
from apps.dashboard.pages import render_crawler_optimizer_page
from tests.test_dashboard_business_pages import fake_streamlit_module


def _transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/crawler-optimizer/analyze":
            return httpx.Response(200, json=_optimizer_payload())
        if request.url.path == "/crawler-optimizer/analyze-source":
            return httpx.Response(200, json={**_optimizer_payload(), "sources_analyzed": 1})
        if request.url.path == "/crawler-optimizer/connector-spec":
            return httpx.Response(
                200,
                json={
                    "status": "ok",
                    "connector_spec": _optimizer_payload()["connector_specs"][0],
                    "markdown": "# Connector Spec\n\nSafe summary only.",
                },
            )
        return httpx.Response(404, json={"detail": "not found"})

    return httpx.MockTransport(handler)


def test_dashboard_client_calls_crawler_optimizer_apis_with_mock_transport() -> None:
    client = DashboardApiClient("http://localhost:8000", transport=_transport())

    analyze = client.crawler_optimizer_analyze(country="United States", disease="norovirus")
    source = client.crawler_optimizer_analyze_source(source_id="1", country="United States", disease="norovirus")
    spec = client.crawler_optimizer_connector_spec(source_id="1", markdown=True)

    assert analyze["status"] == "ok"
    assert source["sources_analyzed"] == 1
    assert spec["connector_spec"]["requires_human_review"] is True
    _assert_safe(analyze)
    _assert_safe(source)
    _assert_safe(spec)


def test_dashboard_optimizer_formatters_compact_payload() -> None:
    payload = _optimizer_payload()

    summary = compact_optimizer_summary(payload)
    failures = rows_from_optimizer_failure_cases(payload)
    actions = rows_from_optimizer_actions(payload)
    specs = rows_from_connector_specs(payload)

    assert summary["failure_case_count"] == 1
    assert failures[0]["failure_type"] == "parser_failed"
    assert actions[0]["action_type"] == "switch_parser"
    assert specs[0]["connector_name"] == "fixture_connector"
    _assert_safe({"summary": summary, "failures": failures, "actions": actions, "specs": specs})


def test_crawler_optimizer_page_executes_with_fake_streamlit_without_service() -> None:
    client = DashboardApiClient("http://localhost:8000", transport=_transport())

    with fake_streamlit_module() as fake:
        render_crawler_optimizer_page(client)

    rendered = str(fake.outputs)
    assert "Crawler Optimizer" in rendered
    assert "requires_human_review=True" in rendered
    assert "不会自动上线新爬虫" in rendered
    assert "真实网络" in rendered
    _assert_safe({"rendered": rendered})


def _optimizer_payload() -> dict:
    return {
        "status": "ok",
        "sources_analyzed": 1,
        "documents_analyzed": 1,
        "failure_case_count": 1,
        "recommended_action_count": 1,
        "connector_spec_count": 1,
        "top_failure_types": [{"failure_type": "parser_failed", "count": 1}],
        "failure_cases": [
            {
                "failure_type": "parser_failed",
                "severity": "high",
                "confidence": 0.9,
                "source_name": "Fixture Source",
                "url": "https://fixture.invalid/doc",
                "evidence": {"raw_text": "secret token should not render"},
            }
        ],
        "recommended_actions": [
            {
                "action_type": "switch_parser",
                "target_type": "source",
                "target_id": "1",
                "reason": "Parser failed.",
                "confidence": 0.9,
                "risk_level": "medium",
                "requires_human_review": False,
                "metadata_json": {"password": 'REDACTED'},
            }
        ],
        "connector_specs": [
            {
                "connector_name": "fixture_connector",
                "source_name": "Fixture Source",
                "source_type": "official_page",
                "parser_strategy": "generic_html",
                "expected_fields": ["title", "url", "published_at"],
                "sample_url_count": 1,
                "requires_human_review": True,
                "test_fixture_required": True,
                "lesson_json": {"hidden": True},
            }
        ],
        "warnings": [],
        "limitations": "Requires human review.",
    }


def _assert_safe(payload: dict) -> None:
    rendered = json.dumps(payload, ensure_ascii=False).lower()
    for marker in (
        "raw_text",
        "raw_html",
        "<html>",
        "feature_json",
        "lesson_json",
        "provenance_json",
        "metadata_json",
        "secret token",
        "password",
        "ssh_key_path",
    ):
        assert marker not in rendered
