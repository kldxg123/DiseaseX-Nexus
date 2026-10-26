from __future__ import annotations

import json

from scripts.export_deepseek_human_review_template import main as export_main
from scripts.import_deepseek_human_review_annotations import main as import_main
from tests.deepseek_human_review_fixture import create_human_review_fixture


def test_human_review_cli_export_and_import_unfilled_template(tmp_path, capsys) -> None:
    report_dir, _database_url, output_dir = create_human_review_fixture(tmp_path)

    assert export_main(["--report-dir", str(report_dir), "--output-dir", str(output_dir)]) == 0
    export_output = capsys.readouterr().out
    assert json.loads(export_output)["template_rows"] == 3

    template = output_dir / "deepseek_human_review_template.json"
    assert (
        import_main(
            [
                "--report-dir",
                str(report_dir),
                "--annotation-file",
                str(template),
                "--output-dir",
                str(output_dir),
            ]
        )
        == 0
    )
    import_output = capsys.readouterr().out
    payload = json.loads(import_output)
    assert payload["imported_annotations"] == 3
    assert payload["promoted_epidemic_events"] == 0
    assert "prompt" not in import_output.lower()
    assert "completion" not in import_output.lower()

