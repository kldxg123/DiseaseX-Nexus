from __future__ import annotations

import json
from pathlib import Path

from src.reports.v3_retraining_readiness_after_p1c import build_v3_retraining_readiness_after_p1c


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_v3_readiness_after_p1c_blocks_without_data_gates(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    reports = tmp_path / "data/outputs/reports"
    _write_json(
        reports / "v3_retraining_readiness_report.json",
        {
            "train_eligible_external_outbreaks": {},
            "new_trainable_sample_count": 0,
            "new_trainable_up_sample_count": 0,
            "hard_positive_count": 0,
            "hard_stable_decline_count": 0,
            "external_test_retained": True,
            "leakage_detected": False,
        },
    )
    _write_json(reports / "drc_health_zone_mapping_report.json", {"mapping_coverage": 0.0, "drc_case_normalization_allowed": False})
    _write_json(reports / "expert_error_review_queue_report.json", {"actual_count": 10})
    _write_json(reports / "preference_pair_annotation_queue_report.json", {"actual_count": 10})
    result = build_v3_retraining_readiness_after_p1c()
    assert result["v3_sft_train_allowed"] is False
    assert result["v3_preference_train_allowed"] is False
    assert result["v3_full_step_update_allowed"] is False
    assert "need_at_least_2_train_eligible_external_outbreaks" in result["blocked_reason"]
