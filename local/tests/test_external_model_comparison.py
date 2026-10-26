from __future__ import annotations

import json
from pathlib import Path

from src.eval.external_model_comparison import build_external_model_comparison


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_external_model_comparison_labels_sources_and_no_promote(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_json(tmp_path / "data/outputs/reports/external_validation_dataset_report.json", {"status": "ok", "external_test_total": 1, "horizons": {"7d": {"external_test_samples": 1}}})
    _write_json(tmp_path / "data/outputs/reports/external_baseline_eval.json", {"status": "not_available", "reason": "missing_baseline_artifacts"})
    _write_json(tmp_path / "data/outputs/reports/external_guard_eval.json", {"status": "ok", "horizons": {}})
    _write_json(tmp_path / "data/outputs/reports/external_qwen_v1_eval.json", {"status": "blocked", "blocked_reason": "fixture"})
    _write_json(tmp_path / "data/outputs/reports/external_qwen_v2_eval.json", {"status": "blocked", "blocked_reason": "fixture"})
    result = build_external_model_comparison()
    assert result["comparison_limited"] is True
    assert result["promote_allowed"] is False
    assert any("external test" in row["metric_source"] for row in result["rows"])
