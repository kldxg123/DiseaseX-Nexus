from __future__ import annotations

from pathlib import Path


def test_dashboard_demo_exposes_expanded_retrieval_controls() -> None:
    source = Path("apps/dashboard/pages.py").read_text(encoding="utf-8")

    assert "demo_max_search_queries" in source
    assert "demo_max_pages_to_read" in source
    assert "max_readable_pages_success" in source
    assert "prefer_official_and_structured_sources" in source
    assert "stop_when_structured_candidates_found" in source
    assert '"max_search_results_per_query": int(max_search_results)' in source
