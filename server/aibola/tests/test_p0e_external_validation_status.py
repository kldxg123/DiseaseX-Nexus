from __future__ import annotations

import json
from pathlib import Path

from src.reports.external_validation_status import build_p0e_status


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_p0e_status_forces_no_training_or_promote(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    ok = {"status": "ok", "blocked_reason": "none"}
    _write_json(tmp_path / "data/outputs/reports/external_quality_gate_report.json", ok)
    _write_json(tmp_path / "data/outputs/reports/external_validation_dataset_report.json", {**ok, "horizons": {"7d": {"external_test_samples": 1, "label_distribution": {"上升": 1}}}})
    _write_json(tmp_path / "data/outputs/reports/external_baseline_eval.json", {"status": "not_available", "baseline_external_eval_status": "not_available"})
    _write_json(tmp_path / "data/outputs/reports/external_guard_eval.json", ok)
    _write_json(tmp_path / "data/outputs/reports/external_qwen_v1_eval.json", ok)
    _write_json(tmp_path / "data/outputs/reports/external_qwen_v2_eval.json", ok)
    _write_json(tmp_path / "data/outputs/reports/external_model_comparison.json", {**ok, "best_external_strategy": {"strategy": "fixture"}})
    _write_json(tmp_path / "data/outputs/reports/external_error_analysis.json", {**ok, "stable_bias_present": True})
    _write_json(tmp_path / "data/processed/model_registry.json", {"external_validation_update_status": "ok"})
    result = build_p0e_status()
    assert result["status"] == "ok"
    assert result["training_allowed"] is False
    assert result["promote_allowed"] is False
