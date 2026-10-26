from __future__ import annotations

import json

import httpx

from apps.dashboard.api_client import DashboardApiClient
from apps.dashboard.formatters import (
    compact_access_decision,
    compact_access_policy_summary,
    rows_from_access_policies,
)
from apps.dashboard.pages import render_access_guardrails_page
from tests.test_dashboard_business_pages import fake_streamlit_module


def _transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/access-policy":
            return httpx.Response(200, json=_policies_payload())
        if request.url.path == "/access-policy/check":
            return httpx.Response(
                200,
                json={
                    "status": "ok",
                    "decision": {
                        "allowed": False,
                        "reason": "live network access was not explicitly allowed",
                        "warnings": [],
                        "policy_summary": _policies_payload()["policies"][0],
                    },
                    "audit_summary": {"total": 1, "allowed": 0, "denied": 1, "by_source": {"cdc_norostat": 1}},
                    "limitations": "Guardrail decision only. No HTTP request was made.",
                },
            )
        return httpx.Response(404, json={"detail": "not found"})

    return httpx.MockTransport(handler)


def test_dashboard_client_access_guardrail_methods_use_mock_transport() -> None:
    client = DashboardApiClient("http://localhost:8000", transport=_transport())

    policies = client.get_access_policies()
    decision = client.check_access_policy(
        source_key="cdc_norostat",
        url="https://example.invalid/test?token=hidden",
        allow_network=False,
    )

    assert policies["policies_checked"] == 1
    assert decision["decision"]["allowed"] is False
    _assert_safe(policies)
    _assert_safe(decision)


def test_dashboard_access_guardrail_formatters_are_compact_and_safe() -> None:
    policies = _policies_payload()
    decision = {
        "status": "ok",
        "decision": {
            "allowed": False,
            "reason": "robots access check has not passed",
            "warnings": ["safe warning"],
            "policy_summary": policies["policies"][0],
        },
        "audit_summary": {"total": 1, "allowed": 0, "denied": 1},
    }

    rows = rows_from_access_policies(policies)
    summary = compact_access_policy_summary(policies)
    compact_decision = compact_access_decision(decision)

    assert rows[0]["source"] == "cdc_norostat"
    assert summary["policies_checked"] == 1
    assert compact_decision["allowed"] is False
    assert compact_decision["source"] == "cdc_norostat"
    _assert_safe({"rows": rows, "summary": summary, "decision": compact_decision})


def test_access_guardrails_page_executes_with_fake_streamlit_without_service() -> None:
    client = DashboardApiClient("http://localhost:8000", transport=_transport())

    with fake_streamlit_module() as fake:
        render_access_guardrails_page(client)

    rendered = str(fake.outputs)
    assert "Access Guardrails" in rendered
    assert "本页本身不抓取网页" in rendered
    assert "人工审查" in rendered
    _assert_safe({"rendered": rendered})


def _policies_payload() -> dict:
    return {
        "status": "ok",
        "policies_checked": 1,
        "errors": [],
        "policies": [
            {
                "source": "cdc_norostat",
                "name": "CDC NoroSTAT",
                "base_url": "https://example.invalid/cdc-norostat-placeholder",
                "allowed_access_method": "official_page_or_dataset",
                "live_fetch_default_enabled": False,
                "requires_allow_network_flag": True,
                "robots_check_required": True,
                "terms_review_required": True,
                "manual_review_required": True,
                "max_requests_per_run": 3,
                "min_interval_seconds": 2.0,
                "allowed_domains": ["example.invalid"],
                "notes": "safe placeholder",
            }
        ],
        "limitations": "Static local policy summary only.",
    }


def _assert_safe(payload: dict) -> None:
    rendered = json.dumps(payload, ensure_ascii=False).lower()
    for marker in (
        "服务器信息.txt",
        "ssh_key_path",
        "password",
        "token",
        "secret",
        "private key",
        "hidden",
    ):
        assert marker.lower() not in rendered
