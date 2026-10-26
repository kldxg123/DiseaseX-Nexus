from __future__ import annotations

import json

from live_source_search_orchestrator.disease_alias_expander import expand_disease_aliases
from live_source_search_orchestrator.provider_planner import detect_gdelt_cooldown, plan_providers
from live_source_search_orchestrator.types import LiveSourceSearchRequest


def test_detect_gdelt_cooldown_from_recent_safe_report(tmp_path):
    run_dir = tmp_path / "search_measles_united_states"
    run_dir.mkdir()
    (run_dir / "live_source_search_summary.json").write_text(
        json.dumps({"failure_types": {"gdelt": "rate_limited"}}),
        encoding="utf-8",
    )

    assert detect_gdelt_cooldown((str(tmp_path),)) is True


def test_auto_planner_skips_gdelt_when_cooldown_detected(monkeypatch):
    monkeypatch.setattr("live_source_search_orchestrator.provider_planner.detect_gdelt_cooldown", lambda: True)
    request = LiveSourceSearchRequest(
        country="United States",
        disease="measles",
        dry_run=False,
        allow_network=True,
        terms_reviewed=True,
        manual_reviewed=True,
    )

    plan = plan_providers(request, expand_disease_aliases("measles"))

    assert "gdelt" not in plan.providers_planned
    assert {"provider": "gdelt", "reason": "rate_limited_cooldown"} in plan.skipped_providers


def test_include_rate_limited_provider_overrides_cooldown(monkeypatch):
    monkeypatch.setattr("live_source_search_orchestrator.provider_planner.detect_gdelt_cooldown", lambda: True)
    request = LiveSourceSearchRequest(
        country="United States",
        disease="measles",
        dry_run=False,
        allow_network=True,
        terms_reviewed=True,
        manual_reviewed=True,
        include_rate_limited_provider=True,
    )

    plan = plan_providers(request, expand_disease_aliases("measles"))

    assert "gdelt" in plan.providers_planned
