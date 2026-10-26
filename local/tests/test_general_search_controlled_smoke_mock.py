from __future__ import annotations

import argparse

from scripts.run_general_search_controlled_smoke import run_validation


def test_general_search_controlled_smoke_mock_generates_reports(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    args = argparse.Namespace(
        disease="measles",
        country="United States",
        intent="structured_time_series",
        search_backend="mock",
        allow_deepseek_api=True,
        allow_general_web_search=False,
        allow_page_read=False,
        terms_reviewed=True,
        manual_reviewed=True,
        max_search_queries=2,
        max_results_per_query=3,
        max_pages_to_read=0,
        max_total_http_requests=4,
        output_dir="reports/deepseek_web_search",
        database_url="sqlite:///reports/deepseek_web_search/deepseek_web_search.sqlite",
    )

    summary = run_validation(args)
    run_dir = tmp_path / summary["validation_dir"]

    assert summary["status"] == "ok"
    assert summary["selected_provider"] == "mock"
    assert summary["mock_search_used"] is True
    assert summary["external_search_provider_called"] is False
    assert summary["page_read_request_count"] == 0
    assert summary["records_persisted"] > 0
    assert (run_dir / "provider_readiness.json").exists()
    assert (run_dir / "search_results_summary.json").exists()
    assert (run_dir / "visualization_3d_data.json").exists()
