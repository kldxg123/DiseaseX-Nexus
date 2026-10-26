from __future__ import annotations

import json
from pathlib import Path

from scripts.reprocess_live_pilot import main, reprocess_live_pilot
from tests.test_reprocess_live_pilot import _seed_synthetic_pilot


FORBIDDEN_MARKERS = (
    "服务器信息",
    "ssh_key_path",
    "password",
    "token",
    "secret",
    "private key",
    "<html",
    "raw_text",
    "feature_json",
    "lesson_json",
    "provenance_json",
    "metadata_json",
)


def test_reprocess_outputs_are_safe_summaries(tmp_path) -> None:
    pilot_dir, _source_db = _seed_synthetic_pilot(tmp_path, weeks=3)

    result = reprocess_live_pilot(
        pilot_dir=pilot_dir,
        output_dir=tmp_path / "outputs",
        run_timestamp="20260702T120200Z",
    )

    rendered_result = json.dumps(result, ensure_ascii=False).lower()
    for marker in FORBIDDEN_MARKERS:
        assert marker.lower() not in rendered_result

    report_paths = [
        Path(result["output_files"]["reprocess_summary_json"]),
        Path(result["output_files"]["reprocess_summary_md"]),
        Path(result["output_files"]["crawler_optimizer_report_json"]),
        Path(result["output_files"]["comparison_with_original_json"]),
        Path(result["output_files"]["rollback_commands_md"]),
    ]
    for path in report_paths:
        payload = path.read_text(encoding="utf-8").lower()
        for marker in FORBIDDEN_MARKERS:
            assert marker.lower() not in payload


def test_reprocess_cli_prints_safe_json_summary(tmp_path, capsys) -> None:
    pilot_dir, _source_db = _seed_synthetic_pilot(tmp_path, weeks=3)

    exit_code = main(
        [
            "--pilot-dir",
            str(pilot_dir),
            "--output-dir",
            str(tmp_path / "outputs"),
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 0
    assert payload["status"] == "ok"
    assert payload["network_access"] is False
    assert payload["cdc_requested"] is False
    assert payload["feature_rows_h7"] == 2
    rendered = captured.out.lower()
    for marker in FORBIDDEN_MARKERS:
        assert marker.lower() not in rendered
