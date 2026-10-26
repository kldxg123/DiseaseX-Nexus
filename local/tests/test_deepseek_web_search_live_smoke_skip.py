from __future__ import annotations

import argparse
import json

from scripts.run_deepseek_controlled_smoke import run_smoke


def test_live_smoke_skips_without_deepseek_key(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    for name in (
        "DEEPSEEK_API_KEY",
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
        allow_page_read=True,
        terms_reviewed=True,
        manual_reviewed=True,
        max_search_queries=3,
        max_results_per_query=5,
        max_pages_to_read=2,
        max_total_http_requests=8,
        output_dir="reports/deepseek_web_search",
        database_url="sqlite:///reports/deepseek_web_search/deepseek_web_search.sqlite",
    )

    summary = run_smoke(args)
    smoke_dir = tmp_path / summary["live_smoke_dir"]

    assert summary["status"] == "skipped"
    assert summary["skip_reason"] == "missing_deepseek_api_key"
    assert summary["deepseek_call_count"] == 0
    assert summary["search_request_count"] == 0
    assert (smoke_dir / "live_smoke_summary.json").exists()
    rendered = json.dumps(json.loads((smoke_dir / "live_smoke_summary.json").read_text()), ensure_ascii=False)
    assert "sk-" not in rendered
    assert "<html" not in rendered.lower()
