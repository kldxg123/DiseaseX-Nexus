from __future__ import annotations

import json

import pytest

from crawler_agent.access_policy import (
    SourceAccessPolicy,
    decide_live_access,
    load_access_policies,
    safe_policy_summary,
    validate_access_policy,
)


def _policy(**overrides) -> SourceAccessPolicy:
    values = {
        "source_key": "cdc_norostat",
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
        "allowed_domains": ("example.invalid",),
        "disallowed_paths": (),
        "notes": "Placeholder policy.",
    }
    values.update(overrides)
    return SourceAccessPolicy(**values)


def test_example_policy_loads_all_expected_sources() -> None:
    policies = load_access_policies("configs/source_access_policy.example.json")

    assert set(policies) == {
        "who_don",
        "ecdc_surveillance_atlas",
        "ecdc_erviss",
        "cdc_norostat",
        "cdc_fluview",
        "cdc_nors",
        "gdelt",
    }
    assert all(policy.live_fetch_default_enabled is False for policy in policies.values())
    assert all(policy.requires_allow_network_flag is True for policy in policies.values())
    assert all(policy.manual_review_required is True for policy in policies.values())
    assert {"www.cdc.gov", "cdc.gov"}.issubset(set(policies["cdc_norostat"].allowed_domains))
    assert policies["cdc_norostat"].max_requests_per_run <= 3
    assert policies["cdc_norostat"].min_interval_seconds >= 2.0
    assert policies["cdc_fluview"].max_requests_per_run == 1
    assert policies["cdc_fluview"].robots_check_required is True
    assert decide_live_access(policies["cdc_fluview"], allow_network=False).allowed is False


def test_policy_validation_reports_invalid_limits_and_domains() -> None:
    policy = _policy(max_requests_per_run=0, allowed_domains=())

    errors = validate_access_policy(policy)

    assert "max requests per run must be at least 1" in errors
    assert "at least one allowed domain is required" in errors


def test_load_policy_rejects_missing_fields(tmp_path) -> None:
    policy_path = tmp_path / "bad-policy.json"
    policy_path.write_text(json.dumps({"sources": [{"source_key": "missing"}]}), encoding="utf-8")

    with pytest.raises(ValueError, match="missing required fields"):
        load_access_policies(str(policy_path))


def test_decide_live_access_rejects_incomplete_review_steps() -> None:
    policy = _policy()

    assert decide_live_access(policy, allow_network=False).allowed is False
    assert (
        decide_live_access(policy, allow_network=True, manual_reviewed=False).reason
        == "manual review has not been completed"
    )
    assert (
        decide_live_access(policy, allow_network=True, manual_reviewed=True, terms_reviewed=False).reason
        == "terms review has not been completed"
    )
    assert (
        decide_live_access(
            policy,
            allow_network=True,
            manual_reviewed=True,
            terms_reviewed=True,
            robots_allowed=False,
        ).reason
        == "robots access check has not passed"
    )


def test_decide_live_access_rejects_limits_domains_and_paths() -> None:
    policy = _policy(disallowed_paths=("/blocked",))

    assert (
        decide_live_access(
            policy,
            allow_network=True,
            manual_reviewed=True,
            terms_reviewed=True,
            robots_allowed=True,
            request_count=4,
        ).reason
        == "request count exceeds policy limit"
    )
    assert (
        decide_live_access(
            policy,
            allow_network=True,
            manual_reviewed=True,
            terms_reviewed=True,
            robots_allowed=True,
            requested_url="https://outside.invalid/test",
        ).reason
        == "requested URL domain is not allowed by policy"
    )
    assert (
        decide_live_access(
            policy,
            allow_network=True,
            manual_reviewed=True,
            terms_reviewed=True,
            robots_allowed=True,
            requested_url="https://example.invalid/blocked/page",
        ).reason
        == "requested URL path is disallowed by policy"
    )


def test_decide_live_access_allows_when_all_checks_pass() -> None:
    decision = decide_live_access(
        _policy(),
        allow_network=True,
        manual_reviewed=True,
        terms_reviewed=True,
        robots_allowed=True,
        requested_url="https://example.invalid/test",
        request_count=1,
    )

    assert decision.allowed is True
    assert decision.reason == "live access guard checks passed"


def test_policy_summary_sanitizes_query_and_sensitive_notes() -> None:
    summary = safe_policy_summary(
        _policy(
            base_url="https://REDACTED@example.invalid/path?token=value",
            notes="contains token value",
        )
    )

    dumped = json.dumps(summary).lower()
    assert summary["base_url"] == "https://example.invalid/path"
    assert "value" not in dumped
    assert "contains token" not in dumped
