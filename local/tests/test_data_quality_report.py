from __future__ import annotations

import json

from common.db import get_session, init_db
from data_quality_agent.report import build_data_quality_report, render_markdown_report, write_report_files
from tests.test_data_quality_profile import _seed_quality_rows


def test_data_quality_report_renders_markdown_and_safe_json(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'report.db'}"
    init_db(database_url).dispose()
    with get_session(database_url) as session:
        _seed_quality_rows(session)
        report = build_data_quality_report(session, country="United States", disease="norovirus")

    markdown = render_markdown_report(report)
    assert "## Summary" in markdown
    assert "## Source Registry" in markdown
    assert "## Label Audit" in markdown
    assert "future accuracy is not guaranteed" in markdown
    rendered = json.dumps(report, ensure_ascii=False)
    assert "feature_json" not in rendered
    assert "lesson_json" not in rendered
    assert "provenance_json" not in rendered
    assert "metadata_json" not in rendered


def test_write_report_files_creates_json_and_markdown(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'write-report.db'}"
    init_db(database_url).dispose()
    with get_session(database_url) as session:
        _seed_quality_rows(session)
        report = build_data_quality_report(session)

    result = write_report_files(report, output_dir=str(tmp_path / "reports"))

    assert result["report_json"].endswith(".json")
    assert result["report_markdown"].endswith(".md")
    assert result["warnings_count"] >= 0
    assert (tmp_path / "reports").exists()
