from __future__ import annotations

import json

from scripts.check_general_search_provider_readiness import check_readiness


def test_readiness_reports_no_provider_without_printing_keys(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    for name in (
        "BING_SEARCH_API_KEY",
        "BRAVE_SEARCH_API_KEY",
        "SERPAPI_API_KEY",
        "TAVILY_API_KEY",
        "EXA_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    summary = check_readiness(output_dir="reports/deepseek_web_search", requested_provider="auto")
    rendered = json.dumps(summary, ensure_ascii=False).lower()

    assert summary["selected_provider"] == "mock"
    assert summary["can_run_live_web_search"] is False
    assert "api_key" not in rendered or "missing_provider_keys" in rendered
    assert "sk-" not in rendered


def test_readiness_selects_configured_available_provider(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "placeholder-test-value-123456")

    summary = check_readiness(output_dir="reports/deepseek_web_search", requested_provider="auto")

    assert summary["selected_provider"] == "brave"
    assert summary["available_providers"] == ["brave"]
    assert summary["can_run_live_web_search"] is True
    assert (tmp_path / "reports/deepseek_web_search/GENERAL_SEARCH_PROVIDER_READINESS.json").exists()
