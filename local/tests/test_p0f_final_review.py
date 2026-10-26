from __future__ import annotations

import json
from pathlib import Path

from src.eval.metrics import LABEL_STABLE, LABEL_UP
from src.reports.p0f_final_review import (
    build_external_model_selection_review,
    build_p0e_final_review,
    build_v3_training_prerequisites,
)
from src.step.adapter_registry import update_p0f_final_review


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _metric(up_recall: float, macro_f1: float = 0.3) -> dict:
    return {
        "macro_f1": macro_f1,
        "balanced_accuracy": macro_f1,
        "up_recall": up_recall,
        "up_precision": 0.25,
        "false_alarm_rate": 0.1,
        "per_class": {LABEL_UP: {"recall": up_recall, "precision": 0.25}},
        "confusion_matrix": {
            LABEL_UP: {LABEL_UP: 1, LABEL_STABLE: 1},
            LABEL_STABLE: {LABEL_UP: 1, LABEL_STABLE: 2},
        },
    }


def _seed_p0f_inputs(root: Path) -> None:
    reports = root / "data/outputs/reports"
    processed = root / "data/processed"
    _write_json(reports / "external_quality_gate_report.json", {"status": "ok"})
    _write_json(
        reports / "external_validation_dataset_report.json",
        {
            "status": "ok",
            "horizons": {
                "7d": {"status": "ok", "external_test_samples": 3, "label_distribution": {LABEL_UP: 2, LABEL_STABLE: 1}},
                "30d": {"status": "not_available", "external_test_samples": 0, "label_distribution": {}},
            },
        },
    )
    _write_json(reports / "p0e_external_validation_status.json", {"status": "ok", "stable_bias_present": True})
    _write_json(reports / "baseline_artifact_recovery_report.json", {"status": "ok", "baseline_recovery_status": "rebuilt"})
    _write_json(
        reports / "external_baseline_recovery_eval.json",
        {"status": "ok", "metric_source": "P0f external test; recovered P0a baseline", "metrics": _metric(0.2)},
    )
    _write_json(reports / "external_guard_eval.json", {"status": "ok", "horizons": {"7d": {"tri_class_mode": {"metrics": _metric(0.4)}}}})
    _write_json(reports / "external_qwen_v1_eval.json", {"status": "ok", "json_validity_rate": 1.0, "metrics": _metric(0.0)})
    _write_json(reports / "external_qwen_v2_eval.json", {"status": "ok", "json_validity_rate": 1.0, "metrics": _metric(0.03)})
    _write_json(reports / "external_error_analysis.json", {"status": "ok", "stable_bias_present": True})
    _write_json(
        processed / "model_registry.json",
        {
            "adapters": [
                {"adapter_id": "qwen25_lora_20260623_011633", "status": "candidate"},
                {"adapter_id": "qwen25_lora_v2_20260624_010911", "status": "candidate"},
            ],
            "allow_auto_promote": False,
        },
    )
    _write_json(root / "configs/config.yaml", {"step": {"model_registry_path": "data/processed/model_registry.json"}})


def test_p0f_reports_label_external_sources_and_keep_v3_blocked(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _seed_p0f_inputs(tmp_path)
    review = build_p0e_final_review()
    selection = build_external_model_selection_review()
    prereq = build_v3_training_prerequisites()
    assert review["status"] == "ok"
    assert selection["comparison_limited"] is True
    assert all("external" in row["metric_source"].lower() for row in selection["rows"])
    assert selection["promote_allowed"] is False
    assert prereq["v3_train_allowed"] is False


def test_p0f_registry_update_keeps_candidate_hold_no_promote(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _seed_p0f_inputs(tmp_path)
    build_p0e_final_review()
    build_external_model_selection_review()
    build_v3_training_prerequisites()
    _write_json(tmp_path / "data/outputs/reports/school_data_request_draft.json", {"status": "ok"})
    result = update_p0f_final_review("configs/config.yaml")
    registry = json.loads((tmp_path / "data/processed/model_registry.json").read_text(encoding="utf-8"))
    assert result["status"] == "ok"
    assert result["promote_allowed"] is False
    assert registry["review_stage"] == "p0f_external_validation_final_review"
    for record in registry["adapters"]:
        assert record["status"] == "candidate"
        assert record["review_status"] == "hold"
        assert record["promote_allowed"] is False
        assert record["allow_auto_promote"] is False
