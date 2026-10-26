import json
from pathlib import Path

from src.step.adapter_registry import (
    P0C2_REQUIRED_FILES,
    update_p0c3_candidate_review,
    update_p0c4a_candidate_review,
    update_p0c4b_planned_candidate,
)


def test_p0c2_required_files_gate_contains_expected_files():
    assert set(P0C2_REQUIRED_FILES) == {
        "adapter_config.json",
        "adapter_model.safetensors",
        "run_manifest.json",
        "train_metrics.json",
    }


def test_candidate_registry_record_never_promotes():
    record = {
        "adapter_id": "qwen25_lora_20260101_000000",
        "adapter_path": "lora_weights/ebola_qwen25_step/candidates/qwen25_lora_20260101_000000",
        "status": "candidate",
        "promote_allowed": False,
        "allow_auto_promote": False,
    }
    assert record["status"] == "candidate"
    assert record["promote_allowed"] is False
    assert record["allow_auto_promote"] is False


def test_p0c3_registry_update_keeps_candidate_and_no_promote(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs/config.yaml").write_text(
        json.dumps({"step": {"model_registry_path": "data/processed/model_registry.json"}}),
        encoding="utf-8",
    )
    registry_path = tmp_path / "data/processed/model_registry.json"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text(
        json.dumps(
            {
                "allow_auto_promote": False,
                "adapters": [
                    {
                        "adapter_id": "qwen25_lora_20260623_011633",
                        "status": "candidate",
                        "promote_allowed": False,
                        "allow_auto_promote": False,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    reports = tmp_path / "data/outputs/reports"
    reports.mkdir(parents=True)
    (reports / "qwen_candidate_review.json").write_text(
        json.dumps({"promote_gate_result": {"promote_gate_passed": False, "primary_reason": "low_up_recall"}}),
        encoding="utf-8",
    )
    (reports / "qwen_error_analysis.json").write_text(
        json.dumps({"up_recall": 0.29, "up_class_failure_summary": {"up_recall": 0.29}}),
        encoding="utf-8",
    )
    (reports / "baseline_qwen_comparison.json").write_text(
        json.dumps({"comparison_limited": True, "same_split": False}),
        encoding="utf-8",
    )
    (reports / "forgetting_guard_report.json").write_text(
        json.dumps({"cfi_status": "not_fully_available", "reason": "first_candidate_adapter_no_prior_adapter_history"}),
        encoding="utf-8",
    )
    result = update_p0c3_candidate_review("configs/config.yaml")
    assert result["status"] == "ok"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    record = registry["adapters"][0]
    assert record["status"] == "candidate"
    assert record["promote_allowed"] is False
    assert record["allow_auto_promote"] is False
    assert record["promote_recommendation"] == "hold"


def test_p0c4a_registry_update_creates_backup_and_keeps_no_promote(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs/config.yaml").write_text(
        json.dumps({"step": {"model_registry_path": "data/processed/model_registry.json"}}),
        encoding="utf-8",
    )
    registry_path = tmp_path / "data/processed/model_registry.json"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text(
        json.dumps(
            {
                "allow_auto_promote": False,
                "adapters": [
                    {
                        "adapter_id": "qwen25_lora_20260623_011633",
                        "status": "candidate",
                        "promote_allowed": False,
                        "allow_auto_promote": False,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    reports = tmp_path / "data/outputs/reports"
    reports.mkdir(parents=True)
    for name, payload in {
        "p0c3_final_review.json": {"status": "ok"},
        "same_split_baseline_eval.json": {"status": "ok", "same_split_baseline_available": True, "comparison_limited": False},
        "up_recall_slice_analysis.json": {"status": "ok", "answers": {"main_up_misclassification": "上升 -> 稳定"}},
        "qwen_guard_ensemble_eval.json": {
            "status": "ok",
            "best_non_training_strategy": {
                "strategy": "case_growth_safety_guard",
                "metrics": {"up_recall": 0.5, "macro_f1": 0.6, "up_precision": 0.31, "false_alarm_rate": 0.1},
            },
            "recommended_next_action": "evaluate_guarded_candidate_before_retraining",
        },
        "qwen_prompt_evidence_diagnosis.json": {"status": "ok", "diagnosis": {"prompt": "insufficient"}},
    }.items():
        (reports / name).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    result = update_p0c4a_candidate_review("configs/config.yaml")

    assert result["status"] == "ok"
    assert Path(result["backup_path"]).exists()
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    record = registry["adapters"][0]
    assert record["status"] == "candidate"
    assert record["promote_allowed"] is False
    assert record["allow_auto_promote"] is False
    assert record["promote_gate_passed"] is False
    assert registry["allow_auto_promote"] is False


def test_p0c4b_registry_adds_planned_candidate_without_changing_v1(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs/config.yaml").write_text(
        json.dumps({"step": {"model_registry_path": "data/processed/model_registry.json"}}),
        encoding="utf-8",
    )
    registry_path = tmp_path / "data/processed/model_registry.json"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text(
        json.dumps(
            {
                "allow_auto_promote": False,
                "adapters": [
                    {
                        "adapter_id": "qwen25_lora_20260623_011633",
                        "status": "candidate",
                        "promote_allowed": False,
                        "allow_auto_promote": False,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    reports = tmp_path / "data/outputs/reports"
    reports.mkdir(parents=True)
    for name, payload in {
        "qwen_candidate_v2_dataset_report.json": {
            "status": "ok",
            "no_test_leakage": True,
            "v2_train_distribution": {"上升": 2, "下降": 1, "稳定": 1},
            "v2_balanced_train_distribution": {"上升": 2, "下降": 2, "稳定": 2},
        },
        "qwen_candidate_v2_prompt_plan.json": {"status": "ok"},
        "qwen_candidate_v2_training_plan.json": {
            "status": "ok",
            "train_from": "base_model",
            "candidate_configs": {"v2a_rebalance_only": {}, "v2b_rebalance_plus_prompt_instruction": {}},
        },
        "qwen_candidate_v2_eval_plan.json": {"status": "ok", "promote_gate": {"up_recall_min": 0.5}},
        "p0c4a_final_review.json": {"status": "ok"},
    }.items():
        (reports / name).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    result = update_p0c4b_planned_candidate("configs/config.yaml")

    assert result["status"] == "ok"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    v1 = next(item for item in registry["adapters"] if item["adapter_id"] == "qwen25_lora_20260623_011633")
    planned = next(item for item in registry["adapters"] if item["adapter_id"] == "qwen25_lora_v2_planned")
    assert v1["status"] == "candidate"
    assert v1["promote_allowed"] is False
    assert planned["status"] == "planned"
    assert planned["promote_allowed"] is False
    assert planned["allow_auto_promote"] is False
