from __future__ import annotations

import json
from pathlib import Path

from evidence_discovery_agent.cli import main
from evidence_discovery_agent.report import run_mock_evidence_discovery


def test_run_mock_evidence_discovery_writes_safe_reports(tmp_path) -> None:
    result = run_mock_evidence_discovery(
        disease="influenza",
        country="United States",
        target="outbreak_or_case_trend",
        output_dir=str(tmp_path),
    )

    assert result["status"] == "ok"
    assert result["network_accessed"] is False
    assert result["documents_seen"] >= 2
    assert "seasonal_naive" in result["recommended_first_batch_models"]
    for path in result["paths"].values():
        assert Path(path).exists()
    _assert_safe(json.dumps(result, ensure_ascii=False))
    _assert_safe((tmp_path / "M2_M0_EVIDENCE_DISCOVERY_REPORT.md").read_text(encoding="utf-8"))


def test_evidence_discovery_cli_outputs_safe_json(tmp_path, capsys) -> None:
    exit_code = main(
        [
            "run-mock-evidence-discovery",
            "--disease",
            "norovirus",
            "--country",
            "United States",
            "--target",
            "outbreak_or_case_trend",
            "--output-dir",
            str(tmp_path),
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 0
    assert payload["status"] == "ok"
    assert payload["network_accessed"] is False
    assert Path(payload["paths"]["report_json"]).exists()
    _assert_safe(captured.out)


def _assert_safe(rendered: str) -> None:
    lowered = rendered.lower()
    for marker in (
        "<html",
        "raw_text",
        "metadata_json",
        "feature_json",
        "provenance_json",
        "lesson_json",
        "password",
        "token",
        "secret",
        "private key",
    ):
        assert marker not in lowered
