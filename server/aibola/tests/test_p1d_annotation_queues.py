from __future__ import annotations

import json
from pathlib import Path

from src.reports.expert_error_review_queue import build_expert_error_review_queue
from src.reports.preference_pair_annotation_queue import build_preference_pair_annotation_queue


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")


def test_p1d_annotation_queues_use_errors_without_training(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_jsonl(
        tmp_path / "data/processed/step_experience_buffer.jsonl",
        [
            {
                "experience_id": "e1",
                "date": "2021-01-01",
                "country": "Guinea",
                "horizon_days": 7,
                "true_label": "上升",
                "predicted_label": "稳定",
                "confidence": 0.9,
                "risk_score": 0.8,
                "error_type": "up_to_stable",
                "input_features": {"case_features": {"growth_7d": 0.5}},
                "evidence": ["missing growth evidence"],
            }
        ],
    )
    review = build_expert_error_review_queue()
    pairs = build_preference_pair_annotation_queue()
    assert review["actual_count"] == 1
    assert pairs["actual_count"] == 1
    assert review["training_allowed"] is False
    assert pairs["training_allowed"] is False
