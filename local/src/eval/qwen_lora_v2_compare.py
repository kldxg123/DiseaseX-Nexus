from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from src.eval.metrics import LABEL_UP
from src.utils.config import resolve_path
from src.utils.io import ensure_dir, write_json


PROMOTE_GATE = {
    "json_validity_rate_min": 0.98,
    "macro_f1_min": 0.70,
    "up_recall_min": 0.50,
    "up_precision_min": 0.35,
    "false_alarm_rate_max": 0.15,
    "evidence_validity_min": 0.95,
}


def load_json(path: str) -> dict[str, Any]:
    p = resolve_path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def metrics_from_eval(block: dict[str, Any]) -> dict[str, Any]:
    metrics = block.get("metrics") if isinstance(block.get("metrics"), dict) else {}
    per_class = metrics.get("per_class") if isinstance(metrics.get("per_class"), dict) else {}
    up = per_class.get(LABEL_UP, {}) if isinstance(per_class.get(LABEL_UP), dict) else {}
    return {
        "json_validity_rate": block.get("json_validity_rate"),
        "macro_f1": metrics.get("macro_f1"),
        "balanced_accuracy": metrics.get("balanced_accuracy"),
        "up_recall": up.get("recall") if up.get("recall") is not None else metrics.get("up_recall"),
        "up_precision": up.get("precision") if up.get("precision") is not None else metrics.get("up_precision"),
        "false_alarm_rate": metrics.get("false_alarm_rate"),
        "stable_to_up_count": metrics.get("stable_to_up_count"),
        "evidence_validity": block.get("evidence_list_validity_rate"),
        "metric_source": block.get("metric_source"),
    }


def metrics_from_plain(metrics: dict[str, Any], source: str) -> dict[str, Any]:
    per_class = metrics.get("per_class") if isinstance(metrics.get("per_class"), dict) else {}
    up = per_class.get(LABEL_UP, {}) if isinstance(per_class.get(LABEL_UP), dict) else {}
    return {
        "macro_f1": metrics.get("macro_f1"),
        "balanced_accuracy": metrics.get("balanced_accuracy"),
        "up_recall": metrics.get("up_recall", up.get("recall")),
        "up_precision": metrics.get("up_precision", up.get("precision")),
        "false_alarm_rate": metrics.get("false_alarm_rate"),
        "stable_to_up_count": metrics.get("stable_to_up_count"),
        "metric_source": source,
    }


def delta(v2: dict[str, Any], other: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for key in ["macro_f1", "balanced_accuracy", "up_recall", "up_precision", "false_alarm_rate"]:
        if isinstance(v2.get(key), (int, float)) and isinstance(other.get(key), (int, float)):
            out[key + "_delta"] = v2[key] - other[key]
    return out


def promote_gate(metrics: dict[str, Any]) -> dict[str, Any]:
    checks = {
        "json_validity_ok": (metrics.get("json_validity_rate") or 0) >= PROMOTE_GATE["json_validity_rate_min"],
        "macro_f1_ok": (metrics.get("macro_f1") or 0) >= PROMOTE_GATE["macro_f1_min"],
        "up_recall_ok": (metrics.get("up_recall") or 0) >= PROMOTE_GATE["up_recall_min"],
        "up_precision_ok": (metrics.get("up_precision") or 0) >= PROMOTE_GATE["up_precision_min"],
        "false_alarm_ok": metrics.get("false_alarm_rate") is not None and metrics.get("false_alarm_rate") <= PROMOTE_GATE["false_alarm_rate_max"],
        "evidence_validity_ok": (metrics.get("evidence_validity") or 0) >= PROMOTE_GATE["evidence_validity_min"],
    }
    passed = all(checks.values())
    failed = [name for name, ok in checks.items() if not ok]
    return {
        "promote_gate_passed": passed,
        "checks": checks,
        "failed_checks": failed,
        "thresholds": PROMOTE_GATE,
        "promote_allowed": False,
        "allow_auto_promote": False,
        "manual_review_required": True,
    }


def build_comparison() -> dict[str, Any]:
    v2_eval = load_json("data/outputs/reports/qwen_lora_v2_eval_metrics.json")
    if v2_eval.get("status") != "ok":
        return {"status": "blocked", "blocked_reason": "qwen_lora_v2_eval_not_ok", "promote_allowed": False}
    v2_original = metrics_from_eval(v2_eval.get("test_original", {}))
    v2_prompt_view = metrics_from_eval(v2_eval.get("test_v2_prompt_view", {})) if isinstance(v2_eval.get("test_v2_prompt_view"), dict) else {}
    v1_eval = load_json("data/outputs/reports/qwen_lora_eval_metrics.json")
    guard = load_json("data/outputs/reports/qwen_guard_ensemble_eval.json")
    same_split = load_json("data/outputs/reports/same_split_baseline_eval.json")
    p0a = load_json("data/outputs/reports/baseline_results.json")

    references: dict[str, Any] = {}
    if isinstance(v1_eval.get("test"), dict):
        references["v1_qwen_lora"] = metrics_from_eval({**v1_eval["test"], "metric_source": "P0c-2 accepted SFT test eval"})
    guard_best = guard.get("best_non_training_strategy", {})
    guard_metrics = guard_best.get("metrics") if isinstance(guard_best.get("metrics"), dict) else {}
    if guard_metrics:
        references["v1_case_growth_safety_guard"] = metrics_from_plain(guard_metrics, "P0c-4A SFT test non-training guard")
    baseline_metrics = same_split.get("mixed_horizon_aggregate", {}).get("metrics") if isinstance(same_split.get("mixed_horizon_aggregate"), dict) else {}
    if baseline_metrics:
        references["same_split_baseline_mixed_horizon"] = metrics_from_plain(baseline_metrics, "P0c-4A same SFT test split mixed-horizon aggregate")
    p0a_best = p0a.get("best_baseline") if isinstance(p0a.get("best_baseline"), dict) else {}
    if p0a_best:
        references["p0a_baseline"] = {
            "macro_f1": p0a_best.get("macro_f1"),
            "metric_source": "P0a chronological 70/30 holdout best baseline; not same split as SFT test",
            "horizon": p0a_best.get("horizon"),
            "feature_set": p0a_best.get("feature_set"),
            "model": p0a_best.get("model"),
        }

    deltas = {
        name: delta(v2_original, metrics)
        for name, metrics in references.items()
        if metrics.get("metric_source", "").lower().find("sft test") >= 0 or name.startswith("same_split")
    }
    gate = promote_gate(v2_original)
    comparison_limited_reasons = []
    if same_split.get("comparison_limited") is True:
        comparison_limited_reasons.append(same_split.get("comparison_limited_reason", "same_split_baseline_limited"))
    if "p0a_baseline" in references:
        comparison_limited_reasons.append("P0a baseline uses chronological holdout and cannot be directly mixed with SFT test metrics")
    if gate["promote_gate_passed"]:
        recommended = "manual_review_candidate_v2_no_promote"
    elif (v2_original.get("up_recall") or 0) > (references.get("v1_qwen_lora", {}).get("up_recall") or 0):
        recommended = "review_v2_candidate_and_consider_guarded_use_before_any_promotion"
    else:
        recommended = "prepare_next_iteration_with_up_recall_focus"
    return {
        "status": "ok",
        "blocked_reason": "none",
        "metric_source_primary": "v2 SFT test original prompt",
        "v2_original_prompt_metrics": v2_original,
        "v2_prompt_rendered_metrics": v2_prompt_view,
        "references": references,
        "same_split_deltas": deltas,
        "comparison_limited": bool(comparison_limited_reasons),
        "comparison_limited_reason": "; ".join(comparison_limited_reasons) if comparison_limited_reasons else "none",
        **gate,
        "recommended_next_action": recommended,
        "promote_allowed": False,
        "allow_auto_promote": False,
        "manual_review_required": True,
    }


def write_report(result: dict[str, Any]) -> Path:
    reports = ensure_dir(resolve_path("data/outputs/reports"))
    write_json(reports / "qwen_lora_v2_compare.json", result)
    lines = [
        "# Qwen LoRA v2 Comparison Report",
        "",
        f"status: {result['status']}",
        f"blocked_reason: {result.get('blocked_reason', 'none')}",
        f"metric_source_primary: {result.get('metric_source_primary', 'not_available')}",
        f"comparison_limited: {str(result.get('comparison_limited')).lower()}",
        f"comparison_limited_reason: {result.get('comparison_limited_reason')}",
        f"promote_gate_passed: {str(result.get('promote_gate_passed')).lower()}",
        "promote_allowed: false",
        "allow_auto_promote: false",
        "manual_review_required: true",
        f"recommended_next_action: {result.get('recommended_next_action')}",
        "",
        "## Summary JSON",
        "```json",
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True),
        "```",
    ]
    path = reports / "qwen_lora_v2_compare.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare Qwen LoRA v2 against v1, guard, and baselines.")
    parser.parse_args()
    result = build_comparison()
    report = write_report(result)
    print(json.dumps({"status": result["status"], "blocked_reason": result.get("blocked_reason", "none"), "report": report.as_posix()}, ensure_ascii=False))
    return 0 if result["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
