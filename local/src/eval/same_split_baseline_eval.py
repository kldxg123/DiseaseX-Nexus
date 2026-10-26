from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

from src.eval.metrics import LABEL_ORDER, LABEL_UP, classification_metrics
from src.eval.p0c4a_common import FEATURE_SET, cross_horizon_leakage, map_sft_to_trend, metric_bundle
from src.models.baseline_train import (
    LABEL_TO_INT,
    _predict_labels,
    _probabilities_for_labels,
    fit_first_available,
    samples_to_frame,
)
from src.utils.config import resolve_path
from src.utils.io import ensure_dir, write_json, write_jsonl


PREDICTION_PATH = "data/outputs/reports/same_split_baseline_predictions.jsonl"


def _fit_predict(train: list[dict[str, Any]], rows: list[dict[str, Any]], seed: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if len(train) < 10 or len({row.get("label") for row in train}) < 2:
        return {"status": "blocked", "blocked_reason": "not_enough_train_samples_or_classes"}, []
    if not rows:
        return {"status": "blocked", "blocked_reason": "empty_eval_rows"}, []
    x_train, columns = samples_to_frame(train, FEATURE_SET)
    x_eval, _ = samples_to_frame(rows, FEATURE_SET)
    x_eval = x_eval.reindex(columns=columns, fill_value=0.0)
    y_train = np.array([LABEL_TO_INT[str(row["label"])] for row in train], dtype=int)
    model, model_name, attempts = fit_first_available(x_train, y_train, strategy="class_weight_balanced", seed=seed)
    if model is None or model_name is None:
        return {"status": "blocked", "blocked_reason": "all_real_ml_baselines_failed", "fallback_attempts": attempts}, []
    probabilities = _probabilities_for_labels(model, x_eval)
    y_pred = _predict_labels(model, x_eval, probabilities)
    y_true = [str(row["label"]) for row in rows]
    metrics = metric_bundle(y_true, y_pred)
    predictions = []
    for row, pred, probs in zip(rows, y_pred, probabilities or [{} for _ in rows]):
        predictions.append(
            {
                "split": row.get("split"),
                "split_index": row.get("split_index"),
                "sample_id": row.get("sft_sample_id"),
                "country": row.get("country"),
                "adm1": row.get("adm1", ""),
                "date": row.get("date"),
                "horizon_days": row.get("horizon_days"),
                "true_label": row.get("label"),
                "predicted_label": pred,
                "probabilities": probs,
                "model": model_name,
                "feature_set": FEATURE_SET,
            }
        )
    return {
        "status": "ok",
        "blocked_reason": "none",
        "model": model_name,
        "feature_set": FEATURE_SET,
        "train_samples": len(train),
        "eval_samples": len(rows),
        "fallback_attempts": attempts,
        "metrics": metrics,
    }, predictions


def run_same_split(seed: int = 42) -> dict[str, Any]:
    mapped, coverage = map_sft_to_trend()
    leakage = cross_horizon_leakage(mapped)
    test_coverage = float(coverage.get("test", {}).get("coverage", 0.0))
    comparison_limited_reasons: list[str] = []
    if test_coverage < 0.95:
        comparison_limited_reasons.append("test_mapping_coverage_below_95_percent")
    if not leakage.get("leakage_check_passed", False):
        comparison_limited_reasons.append("cross_horizon_country_date_overlap_detected")

    by_horizon: dict[str, Any] = {}
    all_test_true: list[str] = []
    all_test_pred: list[str] = []
    all_predictions: list[dict[str, Any]] = []
    ok_any = False
    for horizon in [7, 14, 21, 30]:
        train = [row for row in mapped["train"] if int(row.get("horizon_days") or 0) == horizon]
        val = [row for row in mapped["val"] if int(row.get("horizon_days") or 0) == horizon]
        test = [row for row in mapped["test"] if int(row.get("horizon_days") or 0) == horizon]
        val_result, val_predictions = _fit_predict(train, val, seed)
        test_result, test_predictions = _fit_predict(train, test, seed)
        by_horizon[f"{horizon}d"] = {
            "train_samples": len(train),
            "val_samples": len(val),
            "test_samples": len(test),
            "validation": val_result,
            "test": test_result,
        }
        all_predictions.extend(val_predictions)
        all_predictions.extend(test_predictions)
        if test_result.get("status") == "ok":
            ok_any = True
            all_test_true.extend([str(item["true_label"]) for item in test_predictions])
            all_test_pred.extend([str(item["predicted_label"]) for item in test_predictions])

    aggregate = metric_bundle(all_test_true, all_test_pred) if all_test_true else {}
    write_jsonl(resolve_path(PREDICTION_PATH), all_predictions)
    return {
        "status": "ok" if ok_any else "blocked",
        "blocked_reason": "none" if ok_any else "all_same_split_baselines_failed",
        "same_split_baseline_available": bool(ok_any and test_coverage >= 0.95),
        "comparison_limited": bool(comparison_limited_reasons),
        "comparison_limited_reason": ",".join(comparison_limited_reasons) if comparison_limited_reasons else "none",
        "metric_source": "same SFT train/val/test split; per-horizon baseline models; mixed-horizon aggregate labeled separately",
        "train_mapping_coverage": coverage.get("train", {}).get("coverage", 0.0),
        "val_mapping_coverage": coverage.get("val", {}).get("coverage", 0.0),
        "test_mapping_coverage": coverage.get("test", {}).get("coverage", 0.0),
        "unmapped_count_by_split": {split: item.get("unmapped_count", 0) for split, item in coverage.items()},
        "unmapped_reason_summary": {split: item.get("unmapped_reason_summary", {}) for split, item in coverage.items()},
        "mapping_coverage": coverage,
        "cross_horizon_leakage": leakage,
        "feature_set": FEATURE_SET,
        "horizons": by_horizon,
        "mixed_horizon_aggregate": {
            "label": "mixed-horizon aggregate across 7/14/21/30 test rows",
            "metrics": aggregate,
            "test_samples": len(all_test_true),
        },
        "predictions_path": PREDICTION_PATH,
    }


def write_report(result: dict[str, Any]) -> Path:
    reports = ensure_dir(resolve_path("data/outputs/reports"))
    write_json(reports / "same_split_baseline_eval.json", result)
    lines = [
        "# Same-split Baseline Eval",
        "",
        f"status: {result['status']}",
        f"blocked_reason: {result['blocked_reason']}",
        f"same_split_baseline_available: {str(result['same_split_baseline_available']).lower()}",
        f"comparison_limited: {str(result['comparison_limited']).lower()}",
        f"comparison_limited_reason: {result['comparison_limited_reason']}",
        f"train_mapping_coverage: {result['train_mapping_coverage']:.4f}",
        f"val_mapping_coverage: {result['val_mapping_coverage']:.4f}",
        f"test_mapping_coverage: {result['test_mapping_coverage']:.4f}",
        "",
        "| horizon | model | test_samples | macro-F1 | balanced accuracy | up recall | up precision |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for horizon, payload in result["horizons"].items():
        test = payload.get("test", {})
        if test.get("status") != "ok":
            lines.append(f"| {horizon} | blocked | {payload.get('test_samples', 0)} |  |  |  |  |")
            continue
        metrics = test["metrics"]
        lines.append(
            f"| {horizon} | {test['model']} | {payload['test_samples']} | {metrics['macro_f1']:.4f} | "
            f"{metrics['balanced_accuracy']:.4f} | {metrics['up_recall']:.4f} | {metrics['up_precision']:.4f} |"
        )
    lines.extend(
        [
            "",
            "## Mixed-horizon Aggregate",
            "```json",
            json.dumps(result["mixed_horizon_aggregate"], ensure_ascii=False, indent=2, sort_keys=True),
            "```",
            "",
            "## Mapping And Leakage",
            "```json",
            json.dumps(
                {
                    "mapping_coverage": result["mapping_coverage"],
                    "cross_horizon_leakage": result["cross_horizon_leakage"],
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            "```",
        ]
    )
    path = reports / "same_split_baseline_eval.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate same-split baseline on Qwen SFT splits.")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    result = run_same_split(seed=args.seed)
    report = write_report(result)
    print(json.dumps({"status": result["status"], "blocked_reason": result["blocked_reason"], "report": report.as_posix()}, ensure_ascii=False))
    return 0 if result["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
