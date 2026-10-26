from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.utils.config import load_config, resolve_path
from src.utils.io import ensure_parent, read_jsonl, write_jsonl
from src.utils.time import utc_now_iso


BASE_MODEL = "Qwen/Qwen2.5-7B-Instruct"
LOCAL_BASE_MODEL_PATH = "models/Qwen2.5-7B-Instruct"
P0C2_REQUIRED_FILES = ["adapter_config.json", "adapter_model.safetensors", "run_manifest.json", "train_metrics.json"]


def default_registry() -> dict[str, Any]:
    return {
        "base_model": BASE_MODEL,
        "local_base_model_path": LOCAL_BASE_MODEL_PATH,
        "allow_auto_promote": False,
        "adapters": [],
        "initialized_at": utc_now_iso(),
    }


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def p0c1_blocked_reason() -> str:
    reports = resolve_path("data/outputs/reports")
    search = _load_json(reports / "local_qwen_model_candidates.json")
    remote_search = _load_json(reports / "remote_qwen_model_candidates.json")
    upload = _load_json(reports / "qwen_model_upload_report.json")
    validation = _load_json(reports / "qwen_remote_model_validation_report.json")
    sft = _load_json(reports / "qwen_sft_schema_report.json")
    hardware = _load_json(reports / "hardware_report.json")
    reasons = []
    search_ok = search.get("status") == "ok" or remote_search.get("status") == "ok"
    if not search_ok and (search or remote_search):
        return remote_search.get("blocked_reason") or search.get("blocked_reason", "qwen_search_not_ok")
    if upload.get("upload_status") not in {None, "ok"}:
        return upload.get("blocked_reason", "qwen_upload_not_ok")
    if validation.get("status") not in {None, "ok"}:
        reasons.append(validation.get("blocked_reason", "remote_model_validation_not_ok"))
    if sft.get("status") not in {None, "ok"}:
        reasons.append(sft.get("blocked_reason", "sft_schema_not_ok"))
    if hardware and not hardware.get("qlora_hardware_allowed", False):
        reasons.append(hardware.get("blocked_reason", "hardware_not_allowed"))
    return ",".join(reason for reason in reasons if reason and reason != "none")


def init_registry(config_path: str = "configs/config.yaml") -> dict[str, Any]:
    cfg = load_config(config_path)
    registry_path = ensure_parent(resolve_path(cfg["step"]["model_registry_path"]))
    registry = default_registry()
    blocked_reason = p0c1_blocked_reason()
    if blocked_reason:
        registry["adapters"].append(
            {
                "adapter_id": "p0c1_blocked",
                "base_model": BASE_MODEL,
                "adapter_path": None,
                "status": "blocked",
                "created_at": utc_now_iso(),
                "metrics": {},
                "cfi": None,
                "promote_allowed": False,
                "blocked_reason": blocked_reason,
            }
        )
    registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return {"status": "ok", "registry_path": registry_path.as_posix(), "blocked_record_added": bool(blocked_reason), "blocked_reason": blocked_reason or "none"}


def register_candidate(path: str, metrics: dict, registry_path: str) -> dict:
    record = {
        "created_at": utc_now_iso(),
        "adapter_path": path,
        "status": "candidate",
        "metrics": metrics,
        "allow_auto_promote": False,
    }
    records = read_jsonl(registry_path)
    records.append(record)
    write_jsonl(registry_path, records)
    return record


def register_p0c2_candidate(config_path: str = "configs/config.yaml") -> dict[str, Any]:
    cfg = load_config(config_path)
    registry_path = ensure_parent(resolve_path(cfg["step"]["model_registry_path"]))
    registry = _load_json(registry_path) or default_registry()
    reports = resolve_path("data/outputs/reports")
    train_report = _load_json(reports / "qwen_lora_train_report.json")
    eval_metrics = _load_json(reports / "qwen_lora_eval_metrics.json")
    adapter_path = train_report.get("candidate_adapter_path")
    if train_report.get("training_status") != "completed":
        return {
            "status": "blocked",
            "blocked_reason": "training_not_completed",
            "registry_status": "not_candidate_yet",
            "promote_allowed": False,
        }
    if not adapter_path or adapter_path == "not_registered_until_training_completes":
        return {
            "status": "blocked",
            "blocked_reason": "candidate_adapter_path_missing",
            "registry_status": "not_candidate_yet",
            "promote_allowed": False,
        }
    adapter_dir = resolve_path(adapter_path)
    missing = [name for name in P0C2_REQUIRED_FILES if not (adapter_dir / name).exists()]
    if missing:
        return {
            "status": "blocked",
            "blocked_reason": "missing_candidate_artifacts:" + ",".join(missing),
            "registry_status": "not_candidate_yet",
            "promote_allowed": False,
        }
    adapter_id = adapter_dir.name
    existing = [item for item in registry.get("adapters", []) if item.get("adapter_id") == adapter_id]
    record = {
        "adapter_id": adapter_id,
        "base_model": BASE_MODEL,
        "adapter_path": adapter_path,
        "status": "candidate",
        "created_at": utc_now_iso(),
        "metrics": eval_metrics.get("test", {}).get("metrics", {}),
        "json_validity_rate": eval_metrics.get("test", {}).get("json_validity_rate"),
        "promote_allowed": False,
        "allow_auto_promote": False,
        "blocked_reason": "none",
    }
    if existing:
        registry["adapters"] = [record if item.get("adapter_id") == adapter_id else item for item in registry.get("adapters", [])]
    else:
        registry.setdefault("adapters", []).append(record)
    registry["allow_auto_promote"] = False
    registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return {
        "status": "ok",
        "blocked_reason": "none",
        "registry_status": "candidate",
        "adapter_id": adapter_id,
        "adapter_path": adapter_path,
        "promote_allowed": False,
        "registry_path": registry_path.as_posix(),
    }


def update_p0c3_candidate_review(config_path: str = "configs/config.yaml", adapter_id: str = "qwen25_lora_20260623_011633") -> dict[str, Any]:
    cfg = load_config(config_path)
    registry_path = ensure_parent(resolve_path(cfg["step"]["model_registry_path"]))
    registry = _load_json(registry_path) or default_registry()
    reports = resolve_path("data/outputs/reports")
    candidate_review = _load_json(reports / "qwen_candidate_review.json")
    error_analysis = _load_json(reports / "qwen_error_analysis.json")
    comparison = _load_json(reports / "baseline_qwen_comparison.json")
    forgetting = _load_json(reports / "forgetting_guard_report.json")
    eval_metrics = _load_json(reports / "qwen_lora_eval_metrics.json")

    adapters = registry.setdefault("adapters", [])
    existing = next((item for item in adapters if isinstance(item, dict) and item.get("adapter_id") == adapter_id), None)
    if existing is None:
        return {
            "status": "blocked",
            "blocked_reason": "candidate_adapter_missing_from_registry",
            "registry_status": "missing",
            "promote_allowed": False,
        }

    promote_gate = candidate_review.get("promote_gate_result") or {
        "promote_gate_passed": False,
        "primary_reason": "candidate_review_missing",
        "manual_review_required": True,
        "promote_recommendation": "hold",
    }
    existing.update(
        {
            "status": "candidate",
            "promote_allowed": False,
            "allow_auto_promote": False,
            "manual_review_required": True,
            "review_stage": "p0c3_candidate_review",
            "evaluation_summary": {
                "metrics_source": "P0c-2 accepted SFT test eval",
                "macro_f1": eval_metrics.get("test", {}).get("metrics", {}).get("macro_f1") if isinstance(eval_metrics.get("test"), dict) else None,
                "balanced_accuracy": eval_metrics.get("test", {}).get("metrics", {}).get("balanced_accuracy") if isinstance(eval_metrics.get("test"), dict) else None,
                "json_validity_rate": eval_metrics.get("test", {}).get("json_validity_rate") if isinstance(eval_metrics.get("test"), dict) else None,
                "up_recall": (error_analysis.get("up_class_failure_summary", {}) or {}).get("up_recall") or error_analysis.get("up_recall"),
            },
            "promote_gate_result": promote_gate,
            "promote_recommendation": "hold",
            "baseline_comparison": {
                "comparison_limited": comparison.get("comparison_limited", True),
                "same_split": comparison.get("same_split", False),
                "limitation_reason": comparison.get("limitation_reason"),
            },
            "forgetting_guard": {
                "cfi_status": forgetting.get("cfi_status"),
                "reason": forgetting.get("reason"),
                "cfi": forgetting.get("cfi"),
            },
            "blocked_reason": "none",
            "updated_at": utc_now_iso(),
        }
    )
    registry["allow_auto_promote"] = False
    registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return {
        "status": "ok",
        "blocked_reason": "none",
        "registry_status": "candidate",
        "adapter_id": adapter_id,
        "promote_allowed": False,
        "allow_auto_promote": False,
        "promote_recommendation": "hold",
        "registry_path": registry_path.as_posix(),
    }


def update_p0c4a_candidate_review(config_path: str = "configs/config.yaml", adapter_id: str = "qwen25_lora_20260623_011633") -> dict[str, Any]:
    cfg = load_config(config_path)
    registry_path = ensure_parent(resolve_path(cfg["step"]["model_registry_path"]))
    registry = _load_json(registry_path) or default_registry()
    reports = resolve_path("data/outputs/reports")
    final_review = _load_json(reports / "p0c3_final_review.json")
    same_split = _load_json(reports / "same_split_baseline_eval.json")
    slices = _load_json(reports / "up_recall_slice_analysis.json")
    guard = _load_json(reports / "qwen_guard_ensemble_eval.json")
    prompt = _load_json(reports / "qwen_prompt_evidence_diagnosis.json")
    adapters = registry.setdefault("adapters", [])
    existing = next((item for item in adapters if isinstance(item, dict) and item.get("adapter_id") == adapter_id), None)
    if existing is None:
        return {
            "status": "blocked",
            "blocked_reason": "candidate_adapter_missing_from_registry",
            "registry_status": "missing",
            "promote_allowed": False,
        }
    missing_reports = [
        name
        for name, payload in {
            "p0c3_final_review": final_review,
            "same_split_baseline_eval": same_split,
            "up_recall_slice_analysis": slices,
            "qwen_guard_ensemble_eval": guard,
            "qwen_prompt_evidence_diagnosis": prompt,
        }.items()
        if payload.get("status") != "ok"
    ]
    if missing_reports:
        return {
            "status": "blocked",
            "blocked_reason": "p0c4a_reports_not_ok:" + ",".join(missing_reports),
            "registry_status": existing.get("status", "candidate"),
            "promote_allowed": False,
        }
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_path = registry_path.with_name(f"model_registry_backup_{timestamp}.json")
    if registry_path.exists():
        backup_path.write_text(registry_path.read_text(encoding="utf-8"), encoding="utf-8")
    best = guard.get("best_non_training_strategy", {})
    best_metrics = best.get("metrics", {}) if isinstance(best.get("metrics"), dict) else {}
    recommended = guard.get("recommended_next_action") or "prepare_candidate_v2_training_with_up_recall_focus"
    existing.update(
        {
            "status": "candidate",
            "promote_allowed": False,
            "allow_auto_promote": False,
            "promote_gate_passed": False,
            "primary_reason": "low_up_recall",
            "manual_review_required": True,
            "review_stage": "p0c4a_up_recall_guard_review",
            "p0c4a_review_summary": {
                "same_split_baseline_available": same_split.get("same_split_baseline_available", False),
                "comparison_limited": same_split.get("comparison_limited", True),
                "main_up_misclassification_reason": slices.get("answers", {}),
                "best_non_training_strategy": best.get("strategy"),
                "best_strategy_metrics": {
                    "up_recall": best_metrics.get("up_recall"),
                    "macro_f1": best_metrics.get("macro_f1"),
                    "up_precision": best_metrics.get("up_precision"),
                    "false_alarm_rate": best_metrics.get("false_alarm_rate"),
                },
                "prompt_evidence_diagnosis": prompt.get("diagnosis", {}),
            },
            "recommended_next_action": recommended,
            "blocked_reason": "none",
            "updated_at": utc_now_iso(),
        }
    )
    registry["allow_auto_promote"] = False
    tmp_path = registry_path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp_path.replace(registry_path)
    return {
        "status": "ok",
        "blocked_reason": "none",
        "registry_status": "candidate",
        "adapter_id": adapter_id,
        "promote_allowed": False,
        "allow_auto_promote": False,
        "promote_gate_passed": False,
        "recommended_next_action": recommended,
        "registry_path": registry_path.as_posix(),
        "backup_path": backup_path.as_posix(),
    }


def update_p0c4b_planned_candidate(config_path: str = "configs/config.yaml") -> dict[str, Any]:
    cfg = load_config(config_path)
    registry_path = ensure_parent(resolve_path(cfg["step"]["model_registry_path"]))
    registry = _load_json(registry_path) or default_registry()
    reports = resolve_path("data/outputs/reports")
    dataset = _load_json(reports / "qwen_candidate_v2_dataset_report.json")
    prompt = _load_json(reports / "qwen_candidate_v2_prompt_plan.json")
    training = _load_json(reports / "qwen_candidate_v2_training_plan.json")
    eval_plan = _load_json(reports / "qwen_candidate_v2_eval_plan.json")
    p0c4a = _load_json(reports / "p0c4a_final_review.json")
    adapters = registry.setdefault("adapters", [])
    v1 = next((item for item in adapters if isinstance(item, dict) and item.get("adapter_id") == "qwen25_lora_20260623_011633"), None)
    if not v1:
        return {
            "status": "blocked",
            "blocked_reason": "v1_candidate_missing_from_registry",
            "registry_status": "missing",
            "promote_allowed": False,
        }
    missing_reports = [
        name
        for name, payload in {
            "qwen_candidate_v2_dataset_report": dataset,
            "qwen_candidate_v2_prompt_plan": prompt,
            "qwen_candidate_v2_training_plan": training,
            "qwen_candidate_v2_eval_plan": eval_plan,
            "p0c4a_final_review": p0c4a,
        }.items()
        if payload.get("status") != "ok"
    ]
    if missing_reports:
        return {
            "status": "blocked",
            "blocked_reason": "p0c4b_reports_not_ok:" + ",".join(missing_reports),
            "registry_status": "not_updated",
            "promote_allowed": False,
        }
    if dataset.get("no_test_leakage") is not True:
        return {
            "status": "blocked",
            "blocked_reason": "no_test_leakage_not_true",
            "registry_status": "not_updated",
            "promote_allowed": False,
        }
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_path = registry_path.with_name(f"model_registry_backup_{timestamp}.json")
    if registry_path.exists():
        backup_path.write_text(registry_path.read_text(encoding="utf-8"), encoding="utf-8")
    v1.update(
        {
            "status": "candidate",
            "promote_allowed": False,
            "allow_auto_promote": False,
            "promote_gate_passed": False,
        }
    )
    planned_record = {
        "adapter_id": "qwen25_lora_v2_planned",
        "status": "planned",
        "reason": "up_recall_improvement",
        "base_model": BASE_MODEL,
        "planned_train_data": "data/processed/qwen25_sft_train_v2_balanced.jsonl",
        "planned_config": "configs/train_qwen25_lora_v2.yaml",
        "source_candidate": "qwen25_lora_20260623_011633",
        "created_at": utc_now_iso(),
        "promote_allowed": False,
        "allow_auto_promote": False,
        "requires_user_confirmation_before_training": True,
        "dataset_summary": {
            "v2_train_distribution": dataset.get("v2_train_distribution"),
            "v2_balanced_train_distribution": dataset.get("v2_balanced_train_distribution"),
            "no_test_leakage": dataset.get("no_test_leakage"),
        },
        "training_plan": {
            "train_from": training.get("train_from"),
            "candidate_configs": list((training.get("candidate_configs") or {}).keys()),
        },
        "eval_gate": eval_plan.get("promote_gate", {}),
        "blocked_reason": "none",
    }
    existing = False
    for idx, item in enumerate(adapters):
        if isinstance(item, dict) and item.get("adapter_id") == planned_record["adapter_id"]:
            adapters[idx] = {**item, **planned_record, "updated_at": utc_now_iso()}
            existing = True
            break
    if not existing:
        adapters.append(planned_record)
    registry["allow_auto_promote"] = False
    tmp_path = registry_path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp_path.replace(registry_path)
    return {
        "status": "ok",
        "blocked_reason": "none",
        "registry_status": "planned",
        "adapter_id": planned_record["adapter_id"],
        "v1_status": "candidate",
        "promote_allowed": False,
        "allow_auto_promote": False,
        "registry_path": registry_path.as_posix(),
        "backup_path": backup_path.as_posix(),
    }


def update_p0c4b_train_candidate(config_path: str = "configs/config.yaml") -> dict[str, Any]:
    cfg = load_config(config_path)
    registry_path = ensure_parent(resolve_path(cfg["step"]["model_registry_path"]))
    registry = _load_json(registry_path) or default_registry()
    reports = resolve_path("data/outputs/reports")
    train = _load_json(reports / "qwen_lora_v2_train_report.json")
    eval_metrics = _load_json(reports / "qwen_lora_v2_eval_metrics.json")
    comparison = _load_json(reports / "qwen_lora_v2_compare.json")
    adapter_path = train.get("candidate_adapter_path")
    if train.get("training_status") != "completed":
        return {
            "status": "blocked",
            "blocked_reason": "v2_training_not_completed",
            "registry_status": "planned_or_running",
            "promote_allowed": False,
        }
    if not adapter_path or adapter_path == "not_registered_until_training_completes":
        return {
            "status": "blocked",
            "blocked_reason": "v2_candidate_adapter_path_missing",
            "registry_status": "planned",
            "promote_allowed": False,
        }
    adapter_dir = resolve_path(adapter_path)
    missing = [name for name in P0C2_REQUIRED_FILES if not (adapter_dir / name).exists()]
    if missing:
        return {
            "status": "blocked",
            "blocked_reason": "missing_v2_candidate_artifacts:" + ",".join(missing),
            "registry_status": "planned",
            "promote_allowed": False,
        }
    if eval_metrics.get("status") != "ok":
        return {
            "status": "blocked",
            "blocked_reason": "v2_eval_not_ok",
            "registry_status": "planned",
            "promote_allowed": False,
        }
    if comparison.get("status") != "ok":
        return {
            "status": "blocked",
            "blocked_reason": "v2_comparison_not_ok",
            "registry_status": "planned",
            "promote_allowed": False,
        }
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_path = registry_path.with_name(f"model_registry_backup_{timestamp}.json")
    if registry_path.exists():
        backup_path.write_text(registry_path.read_text(encoding="utf-8"), encoding="utf-8")
    adapters = registry.setdefault("adapters", [])
    v1 = next((item for item in adapters if isinstance(item, dict) and item.get("adapter_id") == "qwen25_lora_20260623_011633"), None)
    if v1:
        v1.update(
            {
                "status": "candidate",
                "promote_allowed": False,
                "allow_auto_promote": False,
                "promote_gate_passed": False,
            }
        )
    adapter_id = adapter_dir.name
    test = eval_metrics.get("test_original") if isinstance(eval_metrics.get("test_original"), dict) else {}
    metrics = test.get("metrics") if isinstance(test.get("metrics"), dict) else {}
    record = {
        "adapter_id": adapter_id,
        "base_model": BASE_MODEL,
        "adapter_path": adapter_path,
        "status": "candidate",
        "created_at": utc_now_iso(),
        "source_stage": "p0c4b_train_candidate_v2",
        "source_candidate": "qwen25_lora_20260623_011633",
        "metrics": metrics,
        "metric_source": "P0c-4B v2 SFT test original prompt",
        "json_validity_rate": test.get("json_validity_rate"),
        "v2_prompt_rendered_metrics": (
            eval_metrics.get("test_v2_prompt_view", {}).get("metrics")
            if isinstance(eval_metrics.get("test_v2_prompt_view"), dict)
            else None
        ),
        "comparison_summary": {
            "comparison_limited": comparison.get("comparison_limited"),
            "comparison_limited_reason": comparison.get("comparison_limited_reason"),
            "same_split_deltas": comparison.get("same_split_deltas"),
        },
        "promote_gate_passed": comparison.get("promote_gate_passed", False),
        "promote_allowed": False,
        "allow_auto_promote": False,
        "manual_review_required": True,
        "recommended_next_action": comparison.get("recommended_next_action", "manual_review_candidate_v2_no_promote"),
        "blocked_reason": "none",
        "updated_at": utc_now_iso(),
    }
    found = False
    for idx, item in enumerate(adapters):
        if isinstance(item, dict) and item.get("adapter_id") == adapter_id:
            adapters[idx] = {**item, **record}
            found = True
            break
    if not found:
        adapters.append(record)
    for item in adapters:
        if isinstance(item, dict) and item.get("adapter_id") == "qwen25_lora_v2_planned":
            item.update(
                {
                    "status": "planned",
                    "training_status": "completed_candidate_recorded",
                    "candidate_adapter_id": adapter_id,
                    "promote_allowed": False,
                    "allow_auto_promote": False,
                    "updated_at": utc_now_iso(),
                }
            )
    registry["allow_auto_promote"] = False
    tmp_path = registry_path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp_path.replace(registry_path)
    return {
        "status": "ok",
        "blocked_reason": "none",
        "registry_status": "candidate",
        "adapter_id": adapter_id,
        "adapter_path": adapter_path,
        "v1_status": "candidate",
        "promote_allowed": False,
        "allow_auto_promote": False,
        "manual_review_required": True,
        "registry_path": registry_path.as_posix(),
        "backup_path": backup_path.as_posix(),
    }


def update_p0c5_final_review(config_path: str = "configs/config.yaml") -> dict[str, Any]:
    cfg = load_config(config_path)
    registry_path = ensure_parent(resolve_path(cfg["step"]["model_registry_path"]))
    registry = _load_json(registry_path) or default_registry()
    reports = resolve_path("data/outputs/reports")
    required_reports = {
        "final_model_review": _load_json(reports / "final_model_review.json"),
        "model_selection_report": _load_json(reports / "model_selection_report.json"),
        "up_recall_failure_report": _load_json(reports / "up_recall_failure_report.json"),
        "data_gap_report": _load_json(reports / "data_gap_report.json"),
        "next_iteration_plan": _load_json(reports / "next_iteration_plan.json"),
    }
    missing_or_not_ok = [
        name
        for name, payload in required_reports.items()
        if payload.get("status") != "ok"
    ]
    if missing_or_not_ok:
        return {
            "status": "blocked",
            "blocked_reason": "p0c5_reports_not_ok:" + ",".join(missing_or_not_ok),
            "registry_status": "not_updated",
            "promote_allowed": False,
        }
    adapters = registry.setdefault("adapters", [])
    v1 = next((item for item in adapters if isinstance(item, dict) and item.get("adapter_id") == "qwen25_lora_20260623_011633"), None)
    v2 = next((item for item in adapters if isinstance(item, dict) and item.get("adapter_id") == "qwen25_lora_v2_20260624_010911"), None)
    missing_adapters = []
    if v1 is None:
        missing_adapters.append("qwen25_lora_20260623_011633")
    if v2 is None:
        missing_adapters.append("qwen25_lora_v2_20260624_010911")
    if missing_adapters:
        return {
            "status": "blocked",
            "blocked_reason": "candidate_adapter_missing:" + ",".join(missing_adapters),
            "registry_status": "missing_candidate",
            "promote_allowed": False,
        }
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_path = registry_path.with_name(f"model_registry_backup_{timestamp}.json")
    if registry_path.exists():
        backup_path.write_text(registry_path.read_text(encoding="utf-8"), encoding="utf-8")
    recommendation = {
        "recommended_current_strategy": "baseline_or_case_growth_guard_for_warning_qwen_for_explanation",
        "recommended_next_action": "collect_more_up_hard_cases_before_v3_training",
    }
    for record, reason in [(v1, "low_up_recall"), (v2, "up_recall_decreased")]:
        record.update(
            {
                "status": "candidate",
                "review_status": "hold",
                "reason": reason,
                "promote_allowed": False,
                "allow_auto_promote": False,
                "promote_gate_passed": False,
                "manual_review_required": True,
                "promoted": False,
                "production": False,
                "review_stage": "p0c5_final_review",
                **recommendation,
                "blocked_reason": "none",
                "updated_at": utc_now_iso(),
            }
        )
    registry.update(
        {
            "allow_auto_promote": False,
            "production_adapter_id": None,
            "review_stage": "p0c5_final_review",
            **recommendation,
            "updated_at": utc_now_iso(),
        }
    )
    tmp_path = registry_path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp_path.replace(registry_path)
    return {
        "status": "ok",
        "blocked_reason": "none",
        "registry_status": "candidate_hold",
        "v1_status": v1.get("status"),
        "v2_status": v2.get("status"),
        "v1_review_status": v1.get("review_status"),
        "v2_review_status": v2.get("review_status"),
        "promote_allowed": False,
        "allow_auto_promote": False,
        "recommended_current_strategy": recommendation["recommended_current_strategy"],
        "recommended_next_action": recommendation["recommended_next_action"],
        "registry_path": registry_path.as_posix(),
        "backup_path": backup_path.as_posix(),
    }


def update_p0e_external_validation(config_path: str = "configs/config.yaml") -> dict[str, Any]:
    cfg = load_config(config_path)
    registry_path = ensure_parent(resolve_path(cfg["step"]["model_registry_path"]))
    registry = _load_json(registry_path) or default_registry()
    reports = resolve_path("data/outputs/reports")
    dataset = _load_json(reports / "external_validation_dataset_report.json")
    baseline = _load_json(reports / "external_baseline_eval.json")
    guard = _load_json(reports / "external_guard_eval.json")
    qwen_v1 = _load_json(reports / "external_qwen_v1_eval.json")
    qwen_v2 = _load_json(reports / "external_qwen_v2_eval.json")
    comparison = _load_json(reports / "external_model_comparison.json")
    errors = _load_json(reports / "external_error_analysis.json")
    required = {
        "external_validation_dataset": dataset,
        "external_guard_eval": guard,
        "external_model_comparison": comparison,
        "external_error_analysis": errors,
    }
    missing_or_not_ok = [name for name, payload in required.items() if payload.get("status") != "ok"]
    if missing_or_not_ok:
        return {
            "status": "blocked",
            "blocked_reason": "p0e_reports_not_ok:" + ",".join(missing_or_not_ok),
            "registry_status": "not_updated",
            "promote_allowed": False,
        }
    adapters = registry.setdefault("adapters", [])
    v1 = next((item for item in adapters if isinstance(item, dict) and item.get("adapter_id") == "qwen25_lora_20260623_011633"), None)
    v2 = next((item for item in adapters if isinstance(item, dict) and item.get("adapter_id") == "qwen25_lora_v2_20260624_010911"), None)
    missing_adapters = []
    if v1 is None:
        missing_adapters.append("qwen25_lora_20260623_011633")
    if v2 is None:
        missing_adapters.append("qwen25_lora_v2_20260624_010911")
    if missing_adapters:
        return {
            "status": "blocked",
            "blocked_reason": "candidate_adapter_missing:" + ",".join(missing_adapters),
            "registry_status": "missing_candidate",
            "promote_allowed": False,
        }
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_path = registry_path.with_name(f"model_registry_backup_{timestamp}.json")
    if registry_path.exists():
        backup_path.write_text(registry_path.read_text(encoding="utf-8"), encoding="utf-8")
    best = comparison.get("best_external_strategy") if isinstance(comparison.get("best_external_strategy"), dict) else {}
    summary = {
        "stage": "p0e_external_validation",
        "dataset_status": dataset.get("status"),
        "external_test_samples": {
            horizon: dataset.get("horizons", {}).get(horizon, {}).get("external_test_samples", 0)
            for horizon in ["7d", "14d", "21d", "30d"]
        },
        "baseline_status": baseline.get("baseline_external_eval_status", baseline.get("status")),
        "guard_status": guard.get("status"),
        "qwen_v1_status": qwen_v1.get("status"),
        "qwen_v2_status": qwen_v2.get("status"),
        "comparison_limited": comparison.get("comparison_limited", True),
        "best_external_strategy": best,
        "stable_bias_present": errors.get("stable_bias_present"),
        "external_validation_confidence": "limited",
    }
    recommendation = {
        "recommended_current_strategy": comparison.get("recommended_current_strategy", "v1_guard_for_research_external_review"),
        "external_validation_recommendation": "use_external_results_for_research_review_only_collect_more_data_before_v3",
        "qwen_standalone_warning_status": comparison.get("qwen_standalone_warning_status", "not_recommended"),
        "recommended_next_action": "collect_more_external_outbreak_data_and_expert_review_before_v3_training",
    }
    for record in (v1, v2):
        record.update(
            {
                "status": "candidate",
                "review_status": "hold",
                "promote_allowed": False,
                "allow_auto_promote": False,
                "promote_gate_passed": False,
                "manual_review_required": True,
                "promoted": False,
                "production": False,
                "review_stage": "p0e_external_validation",
                "external_validation_summary": summary,
                **recommendation,
                "blocked_reason": "none",
                "updated_at": utc_now_iso(),
            }
        )
    registry.update(
        {
            "allow_auto_promote": False,
            "production_adapter_id": None,
            "external_validation_update_status": "ok",
            "review_stage": "p0e_external_validation",
            "external_validation_summary": summary,
            **recommendation,
            "updated_at": utc_now_iso(),
        }
    )
    tmp_path = registry_path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp_path.replace(registry_path)
    return {
        "status": "ok",
        "blocked_reason": "none",
        "registry_status": "candidate_external_validation_hold",
        "v1_status": v1.get("status"),
        "v2_status": v2.get("status"),
        "promote_allowed": False,
        "allow_auto_promote": False,
        "recommended_current_strategy": recommendation["recommended_current_strategy"],
        "recommended_next_action": recommendation["recommended_next_action"],
        "registry_path": registry_path.as_posix(),
        "backup_path": backup_path.as_posix(),
    }


def update_p0f_final_review(config_path: str = "configs/config.yaml") -> dict[str, Any]:
    cfg = load_config(config_path)
    registry_path = ensure_parent(resolve_path(cfg["step"]["model_registry_path"]))
    registry = _load_json(registry_path) or default_registry()
    reports = resolve_path("data/outputs/reports")
    required = {
        "p0e_final_review": _load_json(reports / "p0e_final_review.json"),
        "baseline_artifact_recovery": _load_json(reports / "baseline_artifact_recovery_report.json"),
        "external_model_selection_review": _load_json(reports / "external_model_selection_review.json"),
        "school_data_request_draft": _load_json(reports / "school_data_request_draft.json"),
        "v3_training_prerequisites": _load_json(reports / "v3_training_prerequisites.json"),
    }
    missing_or_blocked = [
        name
        for name, payload in required.items()
        if not payload or payload.get("status") == "blocked"
    ]
    if missing_or_blocked:
        return {
            "status": "blocked",
            "blocked_reason": "p0f_reports_not_ok:" + ",".join(missing_or_blocked),
            "registry_status": "not_updated",
            "promote_allowed": False,
        }
    adapters = registry.setdefault("adapters", [])
    v1 = next((item for item in adapters if isinstance(item, dict) and item.get("adapter_id") == "qwen25_lora_20260623_011633"), None)
    v2 = next((item for item in adapters if isinstance(item, dict) and item.get("adapter_id") == "qwen25_lora_v2_20260624_010911"), None)
    missing_adapters = []
    if v1 is None:
        missing_adapters.append("qwen25_lora_20260623_011633")
    if v2 is None:
        missing_adapters.append("qwen25_lora_v2_20260624_010911")
    if missing_adapters:
        return {
            "status": "blocked",
            "blocked_reason": "candidate_adapter_missing:" + ",".join(missing_adapters),
            "registry_status": "missing_candidate",
            "promote_allowed": False,
        }
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_path = registry_path.with_name(f"model_registry_backup_{timestamp}.json")
    if registry_path.exists():
        backup_path.write_text(registry_path.read_text(encoding="utf-8"), encoding="utf-8")
    recommendation = {
        "recommended_current_strategy": "guarded_baseline_or_qwen_guard_for_research_only",
        "recommended_next_action": "collect_external_data_and_expert_review_before_v3",
    }
    external_summary = {
        "stage": "p0f_external_validation_final_review",
        "p0e_final_review_status": required["p0e_final_review"].get("status"),
        "baseline_recovery_status": required["baseline_artifact_recovery"].get("baseline_recovery_status"),
        "comparison_limited": required["external_model_selection_review"].get("comparison_limited", True),
        "best_external_row": required["external_model_selection_review"].get("best_external_row"),
        "v3_train_allowed": required["v3_training_prerequisites"].get("v3_train_allowed", False),
    }
    for record, reason in [(v1, "low_up_recall"), (v2, "up_recall_decreased")]:
        record.update(
            {
                "status": "candidate",
                "external_validation_status": "failed_up_recall",
                "review_status": "hold",
                "reason": reason,
                "promote_allowed": False,
                "allow_auto_promote": False,
                "promote_gate_passed": False,
                "manual_review_required": True,
                "promoted": False,
                "production": False,
                "review_stage": "p0f_external_validation_final_review",
                "external_validation_summary": external_summary,
                **recommendation,
                "blocked_reason": "none",
                "updated_at": utc_now_iso(),
            }
        )
    registry.update(
        {
            "allow_auto_promote": False,
            "production_adapter_id": None,
            "external_validation_update_status": "p0f_hold",
            "review_stage": "p0f_external_validation_final_review",
            "external_validation_summary": external_summary,
            "v3_train_allowed": False,
            **recommendation,
            "updated_at": utc_now_iso(),
        }
    )
    tmp_path = registry_path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp_path.replace(registry_path)
    return {
        "status": "ok",
        "blocked_reason": "none",
        "registry_status": "candidate_hold",
        "v1_status": v1.get("status"),
        "v2_status": v2.get("status"),
        "v1_external_validation_status": v1.get("external_validation_status"),
        "v2_external_validation_status": v2.get("external_validation_status"),
        "promote_allowed": False,
        "allow_auto_promote": False,
        "v3_train_allowed": False,
        "recommended_current_strategy": recommendation["recommended_current_strategy"],
        "recommended_next_action": recommendation["recommended_next_action"],
        "registry_path": registry_path.as_posix(),
        "backup_path": backup_path.as_posix(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Initialize or update the adapter registry.")
    parser.add_argument("--init", action="store_true")
    parser.add_argument("--register-p0c2-candidate", action="store_true")
    parser.add_argument("--update-p0c3-review", action="store_true")
    parser.add_argument("--update-p0c4a-review", action="store_true")
    parser.add_argument("--update-p0c4b-planned", action="store_true")
    parser.add_argument("--update-p0c4b-train-candidate", action="store_true")
    parser.add_argument("--update-p0c5-final-review", action="store_true")
    parser.add_argument("--update-p0e-external-validation", action="store_true")
    parser.add_argument("--update-p0f-final-review", action="store_true")
    parser.add_argument("--adapter-id", default="qwen25_lora_20260623_011633")
    parser.add_argument("--config", default="configs/config.yaml")
    args = parser.parse_args()
    if args.init:
        result = init_registry(args.config)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if args.register_p0c2_candidate:
        result = register_p0c2_candidate(args.config)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] == "ok" else 2
    if args.update_p0c3_review:
        result = update_p0c3_candidate_review(args.config, args.adapter_id)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] == "ok" else 2
    if args.update_p0c4a_review:
        result = update_p0c4a_candidate_review(args.config, args.adapter_id)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] == "ok" else 2
    if args.update_p0c4b_planned:
        result = update_p0c4b_planned_candidate(args.config)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] == "ok" else 2
    if args.update_p0c4b_train_candidate:
        result = update_p0c4b_train_candidate(args.config)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] == "ok" else 2
    if args.update_p0c5_final_review:
        result = update_p0c5_final_review(args.config)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] == "ok" else 2
    if args.update_p0e_external_validation:
        result = update_p0e_external_validation(args.config)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] == "ok" else 2
    if args.update_p0f_final_review:
        result = update_p0f_final_review(args.config)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] == "ok" else 2
    parser.error("Use --init, --register-p0c2-candidate, --update-p0c3-review, --update-p0c4a-review, --update-p0c4b-planned, --update-p0c4b-train-candidate, --update-p0c5-final-review, --update-p0e-external-validation, or --update-p0f-final-review.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
