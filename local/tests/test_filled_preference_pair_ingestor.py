from __future__ import annotations

import json
from pathlib import Path

from src.data.filled_preference_pair_ingestor import validate_preference_pairs


def test_preference_pair_ingestor_validates_jsonl(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "data/manual_completed/preference_pair_annotation_queue.jsonl"
    path.parent.mkdir(parents=True)
    rows = [
        {
            "sample_id": "ok",
            "chosen_template": {"trend": "上升", "evidence": ["growth"]},
            "rejected_from_model": {"trend": "稳定", "evidence": []},
            "preference_reason_template": "chosen mentions growth",
        },
        {
            "sample_id": "bad",
            "chosen_template": {},
            "rejected_from_model": {"trend": "稳定"},
            "preference_reason_template": "",
        },
    ]
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")
    result = validate_preference_pairs()
    assert result["completed_preference_pairs"] == 1
    assert result["preference_pair_validation_passed"] is True
    assert result["file_reports"][0]["errors"]
