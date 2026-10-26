from __future__ import annotations

from apps.dashboard.pages import select_best_source_pack_path, _source_pack_material_summary


def test_dashboard_prefers_structured_source_pack_over_search_results(tmp_path) -> None:
    run_dir = tmp_path / "run_deepseek_flu"
    run_dir.mkdir()
    search = run_dir / "search_results_summary.json"
    page = run_dir / "page_read_summary.json"
    structured = run_dir / "structured_source_pack.json"
    search.write_text('{"results":[]}', encoding="utf-8")
    page.write_text('{"page_reads":[]}', encoding="utf-8")
    structured.write_text('{"sources":[]}', encoding="utf-8")

    assert select_best_source_pack_path(run_dir) == str(structured)


def test_dashboard_marks_search_results_fallback_as_weak(tmp_path) -> None:
    path = tmp_path / "search_results_summary.json"
    path.write_text('{"results":[{"title":"A","url":"https://example.org","domain":"example.org"}]}', encoding="utf-8")

    summary = _source_pack_material_summary(str(path))

    assert summary["is_search_result_fallback"] is True
    assert summary["all_search_result_only"] is True
    assert "search_result_only_material" in summary["blocker_counts"]
