from __future__ import annotations

from source_discovery_agent.live_guard import decide_live_discovery_access, load_live_discovery_policy


def test_live_discovery_guard_denies_without_allow_network_and_allows_reviewed_conditions() -> None:
    policy = load_live_discovery_policy("configs/live_source_discovery_policy.example.json")

    denied = decide_live_discovery_access(
        provider="gdelt",
        policy=policy,
        allow_network=False,
        manual_reviewed=True,
        terms_reviewed=True,
        max_requests=1,
    )
    allowed = decide_live_discovery_access(
        provider="gdelt",
        policy=policy,
        allow_network=True,
        manual_reviewed=True,
        terms_reviewed=True,
        max_requests=1,
    )

    assert denied["allowed"] is False
    assert "allow_network" in denied["reason"]
    assert allowed["allowed"] is True
    assert allowed["policy_summary"]["free_web_search_allowed"] is False
    assert allowed["policy_summary"]["follow_links_allowed"] is False


def test_live_discovery_guard_rejects_unreviewed_or_excessive_requests() -> None:
    policy = load_live_discovery_policy("configs/live_source_discovery_policy.example.json")

    no_review = decide_live_discovery_access(
        provider="gdelt",
        policy=policy,
        allow_network=True,
        manual_reviewed=False,
        terms_reviewed=True,
        max_requests=1,
    )
    too_many = decide_live_discovery_access(
        provider="gdelt",
        policy=policy,
        allow_network=True,
        manual_reviewed=True,
        terms_reviewed=True,
        max_requests=2,
    )

    assert no_review["allowed"] is False
    assert "manual review" in no_review["reason"]
    assert too_many["allowed"] is False
    assert "max_requests" in too_many["reason"]
