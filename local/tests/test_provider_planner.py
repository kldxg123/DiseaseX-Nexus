from __future__ import annotations

from live_source_search_orchestrator.disease_alias_expander import expand_disease_aliases
from live_source_search_orchestrator.provider_planner import plan_providers
from live_source_search_orchestrator.types import LiveSourceSearchRequest


def test_auto_provider_planner_orders_controlled_providers():
    request = LiveSourceSearchRequest(country="United States", disease="cholera", max_total_requests=3)
    plan = plan_providers(request, expand_disease_aliases("cholera"))
    assert plan.providers_planned == ["data_gov", "official_directory", "gdelt"]
    assert plan.max_total_requests == 3


def test_provider_planner_skips_gdelt_on_cooldown():
    request = LiveSourceSearchRequest(country="United States", disease="cholera", max_total_requests=3)
    plan = plan_providers(request, expand_disease_aliases("cholera"), gdelt_cooldown_active=True)
    assert "gdelt" not in plan.providers_planned
    assert plan.skipped_providers[0]["reason"] == "rate_limited_cooldown"


def test_provider_planner_caps_request_budget():
    request = LiveSourceSearchRequest(country="United States", disease="cholera", max_total_requests=99)
    plan = plan_providers(request, expand_disease_aliases("cholera"))
    assert plan.max_total_requests == 3
