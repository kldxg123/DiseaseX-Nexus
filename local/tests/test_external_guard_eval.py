from __future__ import annotations

import json
from pathlib import Path

from src.eval.external_guard_eval import evaluate_guard
from src.eval.metrics import LABEL_UP


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + ("\n" if rows else ""), encoding="utf-8")


def test_external_guard_uses_frozen_thresholds_without_tuning(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_json(
        tmp_path / "data/outputs/reports/qwen_guard_ensemble_eval.json",
        {"strategies": {"case_growth_safety_guard": {"frozen_thresholds": {"growth_threshold": 0.2, "acceleration_threshold": 0.1, "recent_cases_threshold": 1}}}},
    )
    _write_jsonl(
        tmp_path / "data/processed/external_validation/external_test_7d.jsonl",
        [
            {
                "sample_id": "x",
                "true_label": LABEL_UP,
                "features": {"case_features": {"growth_7d": 0.5, "case_acceleration": 0.0, "new_cases_7d_sum": 2}},
            }
        ],
    )
    for horizon in [14, 21, 30]:
        _write_jsonl(tmp_path / f"data/processed/external_validation/external_test_{horizon}d.jsonl", [])
    result = evaluate_guard()
    assert result["status"] == "ok"
    assert result["thresholds_tuned_on_external_test"] is False
    assert result["horizons"]["7d"]["tri_class_mode"]["metrics"]["up_recall"] == 1.0
