from __future__ import annotations

from source_discovery_agent.live_providers import LiveDiscoveryRequest, get_live_discovery_provider

from live_source_search_orchestrator.disease_alias_expander import expand_disease_aliases
from live_source_search_orchestrator.provider_planner import curated_official_directory_url, plan_providers
from live_source_search_orchestrator.types import LiveSourceSearchRequest


def test_common_diseases_have_curated_official_directory_urls():
    for disease in ("measles", "cholera", "dengue", "influenza", "norovirus"):
        assert curated_official_directory_url(disease, expand_disease_aliases(disease)).startswith("https://")


def test_official_directory_unknown_disease_skips_without_curated_url():
    request = LiveSourceSearchRequest(
        country="United States",
        disease="unknown fever",
        providers="official_directory",
        dry_run=False,
        allow_network=True,
        terms_reviewed=True,
        manual_reviewed=True,
    )
    plan = plan_providers(request, expand_disease_aliases("unknown fever"), gdelt_cooldown_active=False)

    assert plan.providers_planned == []
    assert plan.skipped_providers == [{"provider": "official_directory", "reason": "missing_curated_directory_url"}]


def test_official_directory_missing_url_is_invalid_provider_config():
    provider = get_live_discovery_provider("official_directory")
    result = provider.discover(
        LiveDiscoveryRequest(
            country="United States",
            disease="unknown fever",
            provider="official_directory",
            allow_network=True,
            terms_reviewed=True,
            manual_reviewed=True,
            metadata={"execute_live": True},
        )
    )

    assert result.request_count == 0
    assert result.failure_type == "invalid_provider_config"
    assert result.network_accessed is False
