from __future__ import annotations

from source_discovery_agent.live_guard import load_live_discovery_policy, validate_live_discovery_policy


def test_live_source_discovery_policy_loads_with_safe_defaults() -> None:
    policy = load_live_discovery_policy("configs/live_source_discovery_policy.example.json")
    errors = validate_live_discovery_policy(policy)
    providers = {entry["provider"]: entry for entry in policy["providers"]}

    assert errors == []
    assert {"mock", "gdelt", "data_gov", "official_directory"}.issubset(providers)
    assert providers["gdelt"]["live_discovery_default_enabled"] is False
    assert providers["gdelt"]["free_web_search_allowed"] is False
    assert providers["gdelt"]["follow_links_allowed"] is False
    assert providers["gdelt"]["max_requests_per_run"] <= 1
    assert providers["data_gov"]["free_web_search_allowed"] is False
    assert providers["data_gov"]["follow_links_allowed"] is False
    assert providers["data_gov"]["max_requests_per_run"] <= 1
    rendered = str(policy).lower()
    for forbidden in ("password", "token", "secret", "credential"):
        assert forbidden not in rendered


def test_validate_live_discovery_policy_rejects_unsafe_policy() -> None:
    errors = validate_live_discovery_policy(
        {
            "providers": [
                {
                    "provider": "bad",
                    "live_discovery_default_enabled": True,
                    "max_requests_per_run": 10,
                    "follow_links_allowed": True,
                    "free_web_search_allowed": True,
                    "allowed_domains": [],
                }
            ]
        }
    )

    assert any("disabled by default" in error for error in errors)
    assert any("free web search" in error for error in errors)
    assert any("follow links" in error for error in errors)
