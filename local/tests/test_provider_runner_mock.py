from __future__ import annotations

from source_discovery_agent.live_providers.types import LiveDiscoveryProviderResult

from live_source_search_orchestrator.disease_alias_expander import expand_disease_aliases
from live_source_search_orchestrator.provider_planner import plan_providers
from live_source_search_orchestrator.provider_runner import run_provider_plan
from live_source_search_orchestrator.types import LiveSourceSearchRequest


def test_provider_runner_uses_mocked_providers_once(monkeypatch):
    calls: list[str] = []

    class FakeProvider:
        def __init__(self, name: str) -> None:
            self.name = name

        def discover(self, request):
            calls.append(self.name)
            return LiveDiscoveryProviderResult(
                provider=self.name,
                request_count=1,
                search_results=[],
                warnings=["fake 429"] if self.name == "gdelt" else [],
                network_accessed=True,
                metadata={"status_code": 429 if self.name == "gdelt" else 200},
                failure_type="rate_limited" if self.name == "gdelt" else "no_results",
            )

    monkeypatch.setattr(
        "live_source_search_orchestrator.provider_runner.get_live_discovery_provider",
        lambda provider: FakeProvider(provider),
    )
    request = LiveSourceSearchRequest(
        country="United States",
        disease="cholera",
        dry_run=False,
        allow_network=True,
        terms_reviewed=True,
        manual_reviewed=True,
        max_total_requests=3,
        include_rate_limited_provider=True,
    )
    plan = plan_providers(request, expand_disease_aliases("cholera"))
    results = run_provider_plan(plan, request, expand_disease_aliases("cholera"))
    assert calls == ["data_gov", "official_directory", "gdelt"]
    assert sum(result.request_count for result in results) == 3
    assert results[-1].failure_type == "rate_limited"


def test_provider_runner_dry_run_does_not_call_provider(monkeypatch):
    monkeypatch.setattr(
        "live_source_search_orchestrator.provider_runner.get_live_discovery_provider",
        lambda provider: (_ for _ in ()).throw(AssertionError("provider should not be called")),
    )
    request = LiveSourceSearchRequest(country="United States", disease="cholera", dry_run=True)
    plan = plan_providers(request, expand_disease_aliases("cholera"))
    results = run_provider_plan(plan, request, expand_disease_aliases("cholera"))
    assert all(result.request_count == 0 for result in results)
    assert all(result.network_accessed is False for result in results)
