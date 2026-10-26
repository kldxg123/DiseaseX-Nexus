from __future__ import annotations

import argparse
import re

from scripts.run_general_search_controlled_smoke import run_validation


SECRET_LIKE = re.compile(r"\bsk-[A-Za-z0-9][A-Za-z0-9_-]{18,}\b")


def test_general_search_validation_reports_do_not_leak_secret_or_raw_payloads(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    args = argparse.Namespace(
        disease="measles",
        country="United States",
        intent="structured_time_series",
        search_backend="mock",
        allow_deepseek_api=False,
        allow_general_web_search=False,
        allow_page_read=False,
        terms_reviewed=True,
        manual_reviewed=True,
        max_search_queries=1,
        max_results_per_query=2,
        max_pages_to_read=0,
        max_total_http_requests=2,
        output_dir="reports/deepseek_web_search",
        database_url="sqlite:///reports/deepseek_web_search/deepseek_web_search.sqlite",
    )

    summary = run_validation(args)
    run_dir = tmp_path / summary["validation_dir"]
    rendered = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in run_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in {".json", ".md"}
    )
    lowered = rendered.lower()

    assert not SECRET_LIKE.search(rendered)
    assert "<html" not in lowered
    assert "authorization" not in lowered
    assert "full_provider_response_saved" in lowered
