from __future__ import annotations

import pytest

from crawler_agent import InMemoryNetworkAuditLog, LiveFetchGuard, load_access_policies


def test_live_fetch_guard_records_denied_and_allowed_decisions() -> None:
    policies = load_access_policies("configs/source_access_policy.example.json")
    audit_log = InMemoryNetworkAuditLog()
    guard = LiveFetchGuard(policies, audit_log=audit_log)

    denied = guard.check("cdc_norostat", allow_network=False, requested_url="https://example.invalid/a")
    allowed = guard.check(
        "cdc_norostat",
        allow_network=True,
        requested_url="https://example.invalid/a",
        robots_allowed=True,
        terms_reviewed=True,
        manual_reviewed=True,
    )

    assert denied.allowed is False
    assert allowed.allowed is True
    assert audit_log.summary() == {
        "total": 2,
        "allowed": 1,
        "denied": 1,
        "by_source": {"cdc_norostat": 2},
    }


def test_live_fetch_guard_unknown_source_is_denied() -> None:
    guard = LiveFetchGuard(load_access_policies("configs/source_access_policy.example.json"))

    decision = guard.check("missing_source", allow_network=True)

    assert decision.allowed is False
    assert decision.reason == "unknown source access policy"


def test_require_allowed_raises_permission_error_safely() -> None:
    guard = LiveFetchGuard(load_access_policies("configs/source_access_policy.example.json"))

    with pytest.raises(PermissionError, match="manual review has not been completed"):
        guard.require_allowed(
            "cdc_norostat",
            allow_network=True,
            requested_url="https://example.invalid/a",
        )
