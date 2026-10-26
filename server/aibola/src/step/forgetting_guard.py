from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from src.eval.metrics import LABEL_ORDER
from src.utils.config import load_config, resolve_path
from src.utils.io import ensure_dir, read_jsonl, write_json


DEFAULT_ADAPTER_ID = "qwen25_lora_20260623_011633"


def compute_cfi(history_scores: dict[str, dict[str, float]]) -> float:
    if not history_scores:
        return 0.0
    losses = []
    for scores in history_scores.values():
        best = float(scores.get("best_score", scores.get("best", 0.0)) or 0.0)
        current = float(scores.get("current_score", scores.get("current", best)) or 0.0)
        losses.append(max(0.0, best - current))
    return sum(losses) / len(losses)


def can_promote(history_scores: dict[str, dict[str, float]], threshold: float = 0.05) -> dict:
    cfi = compute_cfi(history_scores)
    return {"cfi": cfi, "threshold": threshold, "can_promote": cfi <= threshold}


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def adapter_count(registry: dict[str, Any]) -> int:
    return len([item for item in registry.get("adapters", []) if isinstance(item, dict) and item.get("status") in {"candidate", "production", "rejected"}])


def pseudo_task_analysis(eval_metrics: dict[str, Any], experiences: list[dict[str, Any]]) -> dict[str, Any]:
    test_eval = eval_metrics.get("test", {}) if isinstance(eval_metrics.get("test"), dict) else {}
    metrics = test_eval.get("metrics", {}) if isinstance(test_eval.get("metrics"), dict) else {}
    horizon_metrics = test_eval.get("horizon_metrics", {"status": "not_available", "reason": "missing_horizon_metrics"})
    per_class = metrics.get("per_class", {}) if isinstance(metrics.get("per_class"), dict) else {}
    country_errors: Counter = Counter()
    quality_flags: Counter = Counter()
    for item in experiences:
        if item.get("true_label") != item.get("predicted_label"):
            country_errors[str(item.get("country") or "unknown")] += 1
            for flag in item.get("data_quality_flags") or []:
                quality_flags[str(flag)] += 1
    return {
        "label_recall": {label: (per_class.get(label) or {}).get("recall") for label in LABEL_ORDER},
        "horizon_metrics": horizon_metrics,
        "top_country_error_counts": dict(country_errors.most_common(10)),
        "data_quality_flag_error_counts": dict(quality_flags),
        "experience_count": len(experiences),
    }


def build_forgetting_guard_report(config_path: str, adapter_id: str = DEFAULT_ADAPTER_ID) -> dict[str, Any]:
    cfg = load_config(config_path)
    reports = resolve_path("data/outputs/reports")
    registry = load_json(resolve_path(cfg["step"]["model_registry_path"]))
    eval_metrics = load_json(reports / "qwen_lora_eval_metrics.json")
    experiences = read_jsonl(resolve_path(cfg["step"]["experience_buffer_path"]))
    count = adapter_count(registry)
    if count <= 1:
        cfi_status = "not_fully_available"
        reason = "first_candidate_adapter_no_prior_adapter_history"
        cfi: float | None = None
        can_promote_result = False
    else:
        cfi_status = "available"
        reason = "adapter_history_available"
        history_scores = {}
        for item in registry.get("adapters", []):
            if not isinstance(item, dict):
                continue
            metrics = item.get("metrics", {}) if isinstance(item.get("metrics"), dict) else {}
            if metrics.get("macro_f1") is not None:
                history_scores[str(item.get("adapter_id"))] = {
                    "best_score": float(metrics.get("macro_f1")),
                    "current_score": float(metrics.get("macro_f1")),
                }
        cfi = compute_cfi(history_scores)
        can_promote_result = cfi <= float(cfg["step"].get("cfi_threshold", 0.05))
    return {
        "status": "ok",
        "blocked_reason": "none",
        "adapter_id": adapter_id,
        "cfi_status": cfi_status,
        "reason": reason,
        "cfi": cfi,
        "cfi_threshold": float(cfg["step"].get("cfi_threshold", 0.05)),
        "can_promote_from_cfi": can_promote_result,
        "promote_allowed": False,
        "allow_auto_promote": False,
        "pseudo_task_analysis": pseudo_task_analysis(eval_metrics, experiences),
    }


def write_forgetting_guard_report(result: dict[str, Any]) -> Path:
    reports = ensure_dir("data/outputs/reports")
    write_json(reports / "forgetting_guard_report.json", result)
    lines = [
        "# Forgetting Guard Report",
        "",
        f"status: {result['status']}",
        f"blocked_reason: {result['blocked_reason']}",
        f"adapter_id: {result['adapter_id']}",
        f"cfi_status: {result['cfi_status']}",
        f"reason: {result['reason']}",
        f"promote_allowed: {str(result['promote_allowed']).lower()}",
        f"allow_auto_promote: {str(result['allow_auto_promote']).lower()}",
        "",
        "## Pseudo-task Analysis",
        "```json",
        json.dumps(result["pseudo_task_analysis"], ensure_ascii=False, indent=2, sort_keys=True),
        "```",
    ]
    path = reports / "forgetting_guard_report.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run CFI and forgetting guard for candidate adapter.")
    parser.add_argument("--config", default="configs/config.yaml")
    parser.add_argument("--adapter-id", default=DEFAULT_ADAPTER_ID)
    args = parser.parse_args()
    result = build_forgetting_guard_report(args.config, args.adapter_id)
    report = write_forgetting_guard_report(result)
    print(json.dumps({"status": result["status"], "cfi_status": result["cfi_status"], "blocked_reason": result["blocked_reason"], "report": report.as_posix()}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
