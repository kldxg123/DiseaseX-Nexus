from __future__ import annotations

import json

from deepseek_web_search_agent.human_review_promotion import export_human_review_template
from tests.deepseek_human_review_fixture import create_human_review_fixture


def test_human_review_template_exports_json_csv_and_instructions(tmp_path) -> None:
    report_dir, _database_url, output_dir = create_human_review_fixture(tmp_path)

    result = export_human_review_template(report_dir, output_dir)
    template = json.loads((output_dir / "deepseek_human_review_template.json").read_text(encoding="utf-8"))

    assert result["status"] == "ok"
    assert result["template_rows"] == 3
    assert (output_dir / "deepseek_human_review_template.csv").exists()
    assert (output_dir / "deepseek_human_review_instructions.md").exists()
    first = template["annotations"][0]
    assert "evidence_summary" in first
    assert "annotation" in first
    assert first["annotation"]["reviewer_decision"] == ""

