from __future__ import annotations

import argparse

from scripts.run_general_search_controlled_smoke import run_validation


def test_general_search_live_validation_skips_without_provider_key(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    for name in (
        "BING_SEARCH_API_KEY",
        "BRAVE_SEARCH_API_KEY",
        "SERPAPI_API_KEY",
        "TAVILY_API_KEY",
        "EXA_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    args = argparse.Namespace(
        disease="measles",
        country="United States",
        intent="structured_time_series",
        search_backend="auto",
        allow_deepseek_api=True,
        allow_general_web_search=True,
        allow_page_read=False,
        terms_reviewed=True,
        manual_reviewed=True,
        max_search_queries=3,
        max_results_per_query=5,
        max_pages_to_read=0,
        max_total_http_requests=6,
        output_dir="reports/deepseek_web_search",
        database_url="sqlite:///reports/deepseek_web_search/deepseek_web_search.sqlite",
    )

    summary = run_validation(args)

    assert summary["status"] == "skipped"
    assert summary["skipped_reason"] == "missing_general_search_provider_key"
    assert summary["external_search_provider_called"] is False
    assert summary["search_request_count"] == 0
    assert (tmp_path / summary["validation_dir"] / "external_search_live_summary.json").exists()
