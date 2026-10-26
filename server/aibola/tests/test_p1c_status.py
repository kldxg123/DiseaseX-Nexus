from __future__ import annotations

import json
from pathlib import Path

from src.reports.p1c_status import build_p1c_status


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_p1c_status_blocks_training_and_recommends_manual_acquisition(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    reports = tmp_path / "data/outputs/reports"
    _write_json(reports / "p1b_download_inventory.json", {"status": "ok", "summary": {"downloaded": 1}})
    _write_json(reports / "v3_retraining_readiness_report.json", {"status": "ok", "v3_train_allowed": False, "blocked_reason": "need_data"})
    _write_json(
        reports / "train_blocking_resolution_matrix.json",
        {
            "status": "ok",
            "matrix": [
                {"data_source": "DRC 2018-2020", "current_role": "cross_check_only"},
                {"data_source": "Uganda 2022", "current_role": "cross_check_only_or_excluded"},
                {"data_source": "Guinea 2021", "current_role": "external_test_locked"},
                {"data_source": "West Africa HDX", "current_role": "cross_check_only"},
            ],
        },
    )
    _write_json(reports / "manual_data_acquisition_templates.json", {"status": "ok"})
    _write_json(reports / "outbreak_retraining_design.json", {"status": "ok"})
    result = build_p1c_status()
    assert result["status"] == "ok"
    assert result["training_allowed"] is False
    assert result["v3_train_allowed"] is False
    assert result["manual_or_school_data_request_recommended"] is True
    assert result["outbreak_roles"]["guinea_2021"] == "external_test_locked"
