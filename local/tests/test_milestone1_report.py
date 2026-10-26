from __future__ import annotations

import json
from pathlib import Path

from scripts.generate_milestone1_report import generate_milestone1_report


def test_generate_milestone1_report_uses_safe_summaries(tmp_path) -> None:
    result = generate_milestone1_report(output_dir=str(tmp_path))

    assert result["status"] in {"achieved", "not_achieved"}
    assert "influenza" in result["supported_diseases"]
    assert "norovirus" in result["supported_diseases"]
    assert "csv_dataset" in result["supported_source_formats"]
    assert "dashboard_data_source" in result["supported_source_formats"]
    for name in (
        "MILESTONE1_COMPLETION_REPORT.md",
        "MILESTONE1_COMPLETION_REPORT.json",
        "MILESTONE1_VALIDATION_SUMMARY.md",
        "MILESTONE1_KNOWN_LIMITATIONS.md",
        "MILESTONE1_NEXT_STEPS.md",
    ):
        assert (tmp_path / name).exists()

    rendered = json.dumps(result, ensure_ascii=False) + "\n" + (tmp_path / "MILESTONE1_COMPLETION_REPORT.md").read_text(
        encoding="utf-8"
    )
    lowered = rendered.lower()
    for marker in (
        "<html",
        "</html",
        "raw_text",
        "metadata_json",
        "provenance_json",
        "feature_json",
        "lesson_json",
        "password",
        "token",
        "secret",
        "private key",
    ):
        assert marker not in lowered
