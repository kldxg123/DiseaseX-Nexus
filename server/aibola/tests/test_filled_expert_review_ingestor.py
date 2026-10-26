from __future__ import annotations

import csv
from pathlib import Path

from src.data.filled_expert_review_ingestor import validate_expert_reviews


def test_expert_review_ingestor_counts_valid_completed_rows(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "data/manual_completed/expert_error_review_queue.csv"
    path.parent.mkdir(parents=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["expert_review_label", "expert_comment", "should_trigger_review", "evidence_quality_score"])
        writer.writeheader()
        writer.writerow({"expert_review_label": "上升", "expert_comment": "case growth supports up", "should_trigger_review": "yes", "evidence_quality_score": "0.9"})
        writer.writerow({"expert_review_label": "", "expert_comment": "", "should_trigger_review": "maybe", "evidence_quality_score": "bad"})
    result = validate_expert_reviews()
    assert result["completed_expert_reviews"] == 1
    assert result["expert_review_validation_passed"] is True
    assert result["file_reports"][0]["errors"]
