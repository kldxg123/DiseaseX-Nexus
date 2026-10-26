from __future__ import annotations

import json
from pathlib import Path

from src.reports.p1e_status import build_p1e_status


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_p1e_status_summarizes_package_and_validation(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    reports = tmp_path / "data/outputs/reports"
    _write(reports / "manual_data_completion_package.json", {"package_zip": "data/outputs/reports/manual_data_completion_package.zip"})
    _write(
        reports / "filled_template_validation_status.json",
        {
            "status": "ok",
            "filled_data_present": False,
            "v3_sft_train_allowed": False,
            "v3_preference_train_allowed": False,
            "v3_full_step_update_allowed": False,
            "blocked_reason": "waiting_for_manual_completed_data",
        },
    )
    result = build_p1e_status()
    assert result["status"] == "ok"
    assert result["manual_completion_package_generated"] is True
    assert result["filled_data_present"] is False
    assert result["training_executed"] is False
