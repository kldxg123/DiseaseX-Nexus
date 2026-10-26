from __future__ import annotations

import json
from pathlib import Path

from src.eval.external_error_analysis import build_external_error_analysis
from src.eval.metrics import LABEL_STABLE, LABEL_UP


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")


def test_external_error_analysis_detects_up_to_stable(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_jsonl(tmp_path / "data/outputs/reports/external_qwen_v1_predictions.jsonl", [{"true_label": LABEL_UP, "predicted_label": LABEL_STABLE, "horizon_days": 7, "date": "2021-02-01"}])
    _write_jsonl(tmp_path / "data/outputs/reports/external_qwen_v2_predictions.jsonl", [])
    result = build_external_error_analysis()
    assert result["stable_bias_present"] is True
    assert result["supports_v3_training_now"] is False
