from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from src.eval.metrics import LABEL_UP
from src.utils.config import resolve_path
from src.utils.io import ensure_dir, write_json


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def find_best_baseline(baseline: dict[str, Any]) -> dict[str, Any]:
    for key in ["best_overall", "best", "best_model", "selected_model"]:
        value = baseline.get(key)
        if isinstance(value, dict):
            return value
    results = baseline.get("results")
    if isinstance(results, list) and results:
        def score(item: dict[str, Any]) -> float:
            return float(item.get("macro_f1", item.get("balanced_accuracy", -1)) or -1)

        return max((item for item in results if isinstance(item, dict)), key=score, default={})
    return {}


def metric_from_baseline(summary: dict[str, Any], key: str) -> Any:
    if key in summary:
        return summary[key]
    metrics = summary.get("metrics")
    if isinstance(metrics, dict):
        return metrics.get(key)
    return None


def build_comparison() -> dict[str, Any]:
    reports = resolve_path("data/outputs/reports")
    qwen = load_json(reports / "qwen_lora_eval_metrics.json")
    baseline = load_json(reports / "baseline_results.json")
    qwen_test = qwen.get("test", {}) if isinstance(qwen.get("test"), dict) else {}
    qwen_metrics = qwen_test.get("metrics", {}) if isinstance(qwen_test.get("metrics"), dict) else {}
    qwen_per_class = qwen_metrics.get("per_class", {}) if isinstance(qwen_metrics.get("per_class"), dict) else {}
    best_baseline = find_best_baseline(baseline)
    same_split = False
    reason = "P0a baseline uses chronological 70/30 holdout and P0c-2 Qwen uses SFT test split; no same-split baseline reproduction artifact was found."
    if baseline.get("sft_test_baseline") or baseline.get("same_split_qwen_test"):
        same_split = True
        reason = "same split baseline artifact available"

    baseline_source = "P0a baseline_results chronological 70/30 holdout"
    backtest_source = "P0a backtest_results 5-fold chronological expanding walk-forward"
    qwen_source = "P0c-2 Qwen LoRA SFT test"
    result = {
        "status": "ok" if qwen.get("status") == "ok" and baseline else "blocked",
        "blocked_reason": "none" if qwen.get("status") == "ok" and baseline else "missing_qwen_or_baseline_report",
        "comparison_limited": not same_split,
        "same_split": same_split,
        "limitation_reason": reason,
        "sources": {
            "baseline_results": baseline_source,
            "backtest_results": backtest_source,
            "qwen_results": qwen_source,
        },
        "qwen_sft_test": {
            "macro_f1": qwen_metrics.get("macro_f1"),
            "balanced_accuracy": qwen_metrics.get("balanced_accuracy"),
            "up_recall": (qwen_per_class.get(LABEL_UP) or {}).get("recall"),
            "json_validity_rate": qwen_test.get("json_validity_rate"),
        },
        "p0a_holdout_best": {
            "model": best_baseline.get("model") or best_baseline.get("model_name") or best_baseline.get("estimator"),
            "feature_set": best_baseline.get("feature_set"),
            "horizon": best_baseline.get("horizon") or best_baseline.get("horizon_days"),
            "macro_f1": metric_from_baseline(best_baseline, "macro_f1"),
            "balanced_accuracy": metric_from_baseline(best_baseline, "balanced_accuracy"),
            "source": baseline_source,
        },
        "interpretation": "Use this as source-labeled context only. Do not treat P0a holdout/backtest metrics and Qwen SFT test metrics as directly interchangeable unless a same-split reproduction is added.",
    }
    return result


def write_comparison_report(result: dict[str, Any]) -> Path:
    reports = ensure_dir("data/outputs/reports")
    write_json(reports / "baseline_qwen_comparison.json", result)
    lines = [
        "# Baseline Qwen Comparison",
        "",
        f"status: {result['status']}",
        f"blocked_reason: {result['blocked_reason']}",
        f"same_split: {str(result['same_split']).lower()}",
        f"comparison_limited: {str(result['comparison_limited']).lower()}",
        f"limitation_reason: {result['limitation_reason']}",
        "",
        "## Sources",
        "```json",
        json.dumps(result["sources"], ensure_ascii=False, indent=2, sort_keys=True),
        "```",
        "",
        "## Metrics",
        "```json",
        json.dumps(
            {
                "p0a_holdout_best": result["p0a_holdout_best"],
                "qwen_sft_test": result["qwen_sft_test"],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        "```",
    ]
    path = reports / "baseline_qwen_comparison.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare P0a baseline and Qwen candidate with source labels.")
    parser.parse_args()
    result = build_comparison()
    report = write_comparison_report(result)
    print(json.dumps({"status": result["status"], "blocked_reason": result["blocked_reason"], "report": report.as_posix()}, ensure_ascii=False))
    return 0 if result["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
