from __future__ import annotations

import json
from pathlib import Path

from tests.deepseek_table_review_fixture import create_mock_table_review


FORBIDDEN = (
    "api_key",
    "token",
    "password",
    "ssh_key_path",
    "服务器信息.txt",
    "raw_text",
    "<html",
    "completion",
    "response_body",
    "metadata_json",
    "feature_json",
    "provenance_json",
    "lesson_json",
)


def test_table_review_reports_do_not_contain_sensitive_or_raw_payloads(tmp_path) -> None:
    summary, _review_path = create_mock_table_review(tmp_path)
    run_dir = Path(summary["run_dir"])
    combined = []
    for path in run_dir.iterdir():
        if path.suffix.lower() in {".json", ".md", ".csv"}:
            combined.append(path.read_text(encoding="utf-8").lower())
    rendered = "\n".join(combined)

    for forbidden in FORBIDDEN:
        assert forbidden.lower() not in rendered
    summary_text = json.dumps(summary, ensure_ascii=False).lower()
    assert "deepseek_called" in summary_text
