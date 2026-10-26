from __future__ import annotations

from apps.dashboard.pages import _source_pack_blocker_label, _source_pack_material_summary


def test_dashboard_zero_row_labels_are_human_readable(tmp_path) -> None:
    path = tmp_path / "search_results_summary.json"
    path.write_text('{"results":[{"title":"Overview","url":"https://example.org","domain":"example.org"}]}', encoding="utf-8")

    summary = _source_pack_material_summary(str(path))

    assert summary["all_search_result_only"] is True
    assert "搜索摘要" in _source_pack_blocker_label("search_result_only_material")
    assert "日期" in _source_pack_blocker_label("no_dated_signal")
