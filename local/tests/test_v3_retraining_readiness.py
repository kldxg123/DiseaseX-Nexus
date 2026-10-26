from __future__ import annotations

import json
from pathlib import Path

from src.reports.v3_retraining_readiness import build_readiness_report


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")


def test_v3_readiness_blocks_when_only_external_test_and_cross_check_exist(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    reports = tmp_path / "data/outputs/reports"
    _write_json(reports / "external_data_final_inventory.json", {"downloaded_external_files": [{"path": "worldpop.tmp", "status": "incomplete_tmp"}]})
    _write_json(
        reports / "external_case_normalization_report.json",
        {
            "status": "ok",
            "metric_type_counts": {"case": 100},
            "source_scope_counts": {"west_africa_2014_2016": 70, "guinea_2021_context": 30},
            "usage_counts": {"validation_candidate": 70, "external_test_candidate": 30},
        },
    )
    _write_json(
        reports / "external_case_deduplication_report.json",
        {"status": "ok", "usage_counts": {"validation_candidate": 70, "external_test_candidate": 30}, "duplicate_with_original_count": 70},
    )
    _write_json(reports / "external_panel_build_report.json", {"status": "ok", "external_panel_rows_added": 10})
    _write_json(reports / "external_label_quality_report.json", {"status": "ok"})
    _write_json(reports / "external_quality_gate_report.json", {"status": "ok"})
    _write_json(
        reports / "external_validation_dataset_report.json",
        {
            "status": "ok",
            "external_test_total": 2,
            "future_leakage_detected": False,
            "overlap_with_original_training_detected": False,
            "guinea_2021_policy": "external_test_candidate only; not training data",
        },
    )
    _write_json(reports / "p0e_external_validation_status.json", {"status": "ok", "stable_bias_present": True})
    _write_json(
        reports / "external_error_analysis.json",
        {
            "status": "ok",
            "analyses": {"qwen_v2": {"up_to_stable_count": 2, "up_support": 2}},
            "best_external_strategy": {"per_class": {"稳定": {"support": 1}, "下降": {"support": 1}}},
        },
    )
    for horizon in [7, 14, 21, 30]:
        rows = [
            {
                "sample_id": f"{horizon}_g1",
                "usage": "external_test_candidate",
                "source_dataset": "guinea_2021_hdx",
                "label": "上升",
            }
        ] if horizon == 7 else []
        _write_jsonl(tmp_path / f"data/processed/trend_dataset_external_{horizon}d.jsonl", rows)
    result = build_readiness_report()
    assert result["status"] == "ok"
    assert result["v3_train_allowed"] is False
    assert result["external_test_retained"] is True
    assert result["new_trainable_sample_count"] == 0
    assert result["hard_positive_count"] == 0
    assert "need_200_train_eligible_up_hard_positives" in result["blocked_reason"]
    assert result["role_reallocation"]["external_test_locked"]["label_samples"] == 2
