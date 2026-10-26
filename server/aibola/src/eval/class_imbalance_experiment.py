from __future__ import annotations

import argparse
import json
from typing import Any

import numpy as np

from src.eval.metrics import LABEL_ORDER, LABEL_UP
from src.models.baseline_train import (
    LABEL_TO_INT,
    _predict_labels,
    _probabilities_for_labels,
    chronological_holdout,
    evaluate_predictions,
    fit_first_available,
    samples_to_frame,
)
from src.utils.config import load_config, resolve_path
from src.utils.io import ensure_dir, read_jsonl, write_json


STRATEGIES = ["baseline_none", "class_weight_balanced", "minority_aware_sample_weight", "threshold_tuning"]
FEATURE_SET = "cases_weather_mobility_proxy"


def _apply_up_threshold(probabilities: list[dict[str, float]], threshold: float) -> list[str]:
    predictions: list[str] = []
    for probs in probabilities:
        if probs.get(LABEL_UP, 0.0) >= threshold:
            predictions.append(LABEL_UP)
        else:
            predictions.append(max((item for item in probs.items() if item[0] != LABEL_UP), key=lambda item: item[1])[0])
    return predictions


def _tune_up_threshold(train_samples: list[dict], seed: int) -> tuple[float, list[dict[str, Any]]]:
    if len(train_samples) < 20:
        return 0.34, [{"status": "warning", "reason": "not_enough_training_rows_for_threshold_tuning"}]
    subtrain, validation = chronological_holdout(train_samples, train_fraction=0.8)
    x_train, columns = samples_to_frame(subtrain, FEATURE_SET)
    x_val, _ = samples_to_frame(validation, FEATURE_SET)
    x_val = x_val.reindex(columns=columns, fill_value=0.0)
    y_train = np.array([LABEL_TO_INT[str(sample["label"])] for sample in subtrain], dtype=int)
    y_true = [str(sample["label"]) for sample in validation]
    model, model_name, attempts = fit_first_available(x_train, y_train, strategy="baseline_none", seed=seed)
    if model is None:
        return 0.34, attempts
    probs = _probabilities_for_labels(model, x_val)
    if not probs:
        return 0.34, attempts + [{"model": model_name, "status": "warning", "reason": "predict_proba_not_available_for_threshold_tuning"}]
    best_threshold = 0.34
    best_score = -1.0
    for threshold in [0.15, 0.2, 0.25, 0.3, 0.34, 0.4, 0.5]:
        y_pred = _apply_up_threshold(probs, threshold)
        metrics = evaluate_predictions(y_true, y_pred, probs)
        score = metrics["per_class"][LABEL_UP]["recall"] + 0.25 * metrics["macro_f1"]
        if score > best_score:
            best_score = score
            best_threshold = threshold
    return best_threshold, attempts + [{"model": model_name, "status": "threshold_selected", "threshold": best_threshold, "score": best_score}]


def run_strategy(samples: list[dict], horizon: int, strategy: str, seed: int) -> dict[str, Any]:
    if len(samples) < 10:
        return {"status": "blocked", "blocked_reason": "not_enough_samples", "samples": len(samples)}
    train, test = chronological_holdout(samples)
    threshold = None
    threshold_attempts: list[dict[str, Any]] = []
    model_strategy = strategy
    if strategy == "threshold_tuning":
        threshold, threshold_attempts = _tune_up_threshold(train, seed)
        model_strategy = "baseline_none"
    x_train, columns = samples_to_frame(train, FEATURE_SET)
    x_test, _ = samples_to_frame(test, FEATURE_SET)
    x_test = x_test.reindex(columns=columns, fill_value=0.0)
    y_train = np.array([LABEL_TO_INT[str(sample["label"])] for sample in train], dtype=int)
    y_true = [str(sample["label"]) for sample in test]
    model, model_name, attempts = fit_first_available(x_train, y_train, strategy=model_strategy, seed=seed)
    attempts = threshold_attempts + attempts
    if model is None or model_name is None:
        return {
            "status": "blocked",
            "blocked_reason": "all_real_ml_baselines_failed",
            "horizon": horizon,
            "strategy": strategy,
            "fallback_attempts": attempts,
        }
    probs = _probabilities_for_labels(model, x_test)
    y_pred = _apply_up_threshold(probs, threshold) if strategy == "threshold_tuning" and probs and threshold is not None else _predict_labels(model, x_test, probs)
    metrics = evaluate_predictions(y_true, y_pred, probs)
    return {
        "status": "ok",
        "metric_source": "chronological_70_30_holdout",
        "horizon": horizon,
        "feature_set": FEATURE_SET,
        "strategy": strategy,
        "model": model_name,
        "threshold": threshold,
        "fallback_attempts": attempts,
        "train_samples": len(train),
        "test_samples": len(test),
        "metrics": metrics,
    }


def run_class_imbalance(config_path: str, horizons: list[int]) -> dict[str, Any]:
    cfg = load_config(config_path)
    processed = resolve_path(cfg["paths"]["data_processed"])
    seed = int(cfg["project"].get("random_seed", 42))
    results: dict[str, Any] = {}
    ok_any = False
    for horizon in horizons:
        samples = read_jsonl(processed / f"trend_dataset_{horizon}d.jsonl")
        results[f"{horizon}d"] = {}
        for strategy in STRATEGIES:
            result = run_strategy(samples, horizon, strategy, seed)
            results[f"{horizon}d"][strategy] = result
            ok_any = ok_any or result.get("status") == "ok"
    return {
        "status": "ok" if ok_any else "blocked",
        "blocked_reason": "none" if ok_any else "all_class_imbalance_strategies_failed",
        "metric_source": "chronological_70_30_holdout",
        "feature_set": FEATURE_SET,
        "strategies": STRATEGIES,
        "horizons": results,
    }


def write_class_imbalance_report(result: dict[str, Any], config_path: str) -> None:
    cfg = load_config(config_path)
    reports = ensure_dir(resolve_path(cfg["paths"]["outputs"]) / "reports")
    write_json(reports / "class_imbalance_strategy_report.json", result)
    lines = [
        "# Class Imbalance Strategy Report",
        "",
        f"status: {result['status']}",
        f"blocked_reason: {result['blocked_reason']}",
        "metric_source: chronological_70_30_holdout",
        f"feature_set: {FEATURE_SET}",
        "",
        "| horizon | strategy | model | macro_f1 | balanced_accuracy | up_recall | threshold |",
        "|---|---|---|---:|---:|---:|---:|",
    ]
    for horizon, strategies in result["horizons"].items():
        for strategy, item in strategies.items():
            if item.get("status") != "ok":
                lines.append(f"| {horizon} | {strategy} | blocked |  |  |  |  |")
                continue
            metrics = item["metrics"]
            lines.append(
                f"| {horizon} | {strategy} | {item['model']} | {metrics['macro_f1']:.4f} | "
                f"{metrics['balanced_accuracy']:.4f} | {metrics['per_class'][LABEL_UP]['recall']:.4f} | "
                f"{'' if item.get('threshold') is None else item['threshold']} |"
            )
    (reports / "class_imbalance_strategy_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare P0a class-imbalance strategies.")
    parser.add_argument("--config", default="configs/config.yaml")
    parser.add_argument("--horizons", nargs="*", type=int, default=[7, 14, 21, 30])
    args = parser.parse_args()
    result = run_class_imbalance(args.config, args.horizons)
    write_class_imbalance_report(result, args.config)
    print(json.dumps({"status": result["status"], "blocked_reason": result["blocked_reason"]}, ensure_ascii=False))
    return 0 if result["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
