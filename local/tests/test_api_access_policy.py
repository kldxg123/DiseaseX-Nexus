from __future__ import annotations

import json


FORBIDDEN = (
    "服务器信息.txt",
    "ssh_key_path",
    "password",
    "token",
    "secret",
    "private key",
)


def test_api_access_policy_returns_safe_static_summary(api_client) -> None:
    response = api_client.get("/access-policy")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["policies_checked"] == 7
    assert payload["errors"] == []
    assert len(payload["policies"]) == 7
    assert all(policy["live_fetch_default_enabled"] is False for policy in payload["policies"])
    assert all(policy["requires_allow_network_flag"] is True for policy in payload["policies"])
    _assert_safe(payload)


def test_api_access_policy_check_returns_denied_decision_without_network(api_client) -> None:
    response = api_client.post(
        "/access-policy/check",
        json={
            "source_key": "cdc_norostat",
            "url": "https://example.invalid/test?token=hidden",
            "allow_network": False,
            "terms_reviewed": False,
            "manual_reviewed": False,
            "robots_allowed": None,
            "request_count": 1,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["decision"]["allowed"] is False
    assert payload["decision"]["reason"] == "live network access was not explicitly allowed"
    assert payload["audit_summary"]["denied"] == 1
    _assert_safe(payload)


def test_api_access_policy_check_can_allow_only_after_all_guard_conditions(api_client) -> None:
    response = api_client.post(
        "/access-policy/check",
        json={
            "source_key": "cdc_norostat",
            "url": "https://example.invalid/test",
            "allow_network": True,
            "terms_reviewed": True,
            "manual_reviewed": True,
            "robots_allowed": True,
            "request_count": 1,
        },
    )

    assert response.status_code == 200
    assert response.json()["decision"]["allowed"] is True
    _assert_safe(response.json())


def _assert_safe(payload: dict) -> None:
    rendered = json.dumps(payload, ensure_ascii=False).lower()
    for marker in FORBIDDEN:
        assert marker.lower() not in rendered
    assert "hidden" not in rendered
