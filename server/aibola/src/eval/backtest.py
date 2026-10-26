from __future__ import annotations

import argparse
import json
from statistics import mean
from typing import Any

import numpy as np

from src.eval.metrics import LABEL_UP
from src.models.baseline_train import (
    FEATURE_SETS,
    LABEL_TO_INT,
    _predict_labels,
    _probabilities_for_labels,
    evaluate_predictions,
    fit_first_available,
    samples_to_frame,
)
from src.utils.config import load_config, resolve_path
from src.utils.io import ensure_dir, read_jsonl, write_json


def chronological_expanding_folds(samples: list[dict], folds: int = 5) -> list[tuple[list[dict], list[dict]]]:
    ordered = sorted(samples, key=lambda row: (str(row.get("date", "")), str(row.get("sample_id", ""))))
    if len(ordered) < folds + 5:
        return []
    fold_size = max(1, len(ordered) // (folds + 1))
    output: list[tuple[list[dict], list[dict]]] = []
    for fold_idx in range(folds):
        train_end = fold_size * (fold_idx + 1)
        test_start = train_end
        test_end = len(ordered) if fold_idx == folds - 1 else min(len(ordered), test_start + fold_size)
        train = ordered[:train_end]
        test = ordered[test_start:test_end]
        if train and test:
            output.append((train, test))
    return output


def _summarize_folds(fold_results: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [item for item in fold_results if item.get("status") == "ok"]
    if not ok:
        return {"status": "blocked", "blocked_reason": "no_successful_folds", "folds": fold_results}
    return {
        "status": "ok",
        "blocked_reason": "none",
        "fold_count": len(fold_results),
        "successful_folds": len(ok),
        "macro_f1_mean": mean(item["metrics"]["macro_f1"] for item in ok),
        "balanced_accuracy_mean": mean(item["metrics"]["balanced_accuracy"] for item in ok),
        "up_recall_mean": mean(item["metrics"]["per_class"][LABEL_UP]["recall"] for item in ok),
        "folds": fold_results,
    }


def run_backtest_for(samples: list[dict], horizon: int, feature_set: str, seed: int) -> dict[str, Any]:
    folds = chronological_expanding_folds(samples, folds=5)
    fold_results: list[dict[str, Any]] = []
    for idx, (train, test) in enumerate(folds, start=1):
        x_train, columns = samples_to_frame(train, feature_set)
        x_test, _ = samples_to_frame(test, feature_set)
        x_test = x_test.reindex(columns=columns, fill_value=0.0)
        y_train = np.array([LABEL_TO_INT[str(sample["label"])] for sample in train], dtype=int)
        y_true = [str(sample["label"]) for sample in test]
        model, model_name, attempts = fit_first_available(x_train, y_train, strategy="baseline_none", seed=seed)
        if model is None or model_name is None:
            fold_results.append(
                {
                    "status": "blocked",
                    "fold": idx,
                    "blocked_reason": "all_real_ml_baselines_failed",
                    "fallback_attempts": attempts,
                    "train_samples": len(train),
                    "test_samples": len(test),
                }
            )
            continue
        probabilities = _probabilities_for_labels(model, x_test)
        y_pred = _predict_labels(model, x_test, probabilities)
        fold_results.append(
            {
                "status": "ok",
                "fold": idx,
                "model": model_name,
                "fallback_attempts": attempts,
                "train_samples": len(train),
                "test_samples": len(test),
                "metrics": evaluate_predictions(y_true, y_pred, probabilities),
            }
        )
    summary = _summarize_folds(fold_results)
    summary["metric_source"] = "5_fold_chronological_expanding_walk_forward"
    summary["horizon"] = horizon
    summary["feature_set"] = feature_set
    return summary


def run_backtests(config_path: str, horizons: list[int]) -> dict[str, Any]:
    cfg = load_config(config_path)
    processed = resolve_path(cfg["paths"]["data_processed"])
    seed = int(cfg["project"].get("random_seed", 42))
    results: dict[str, Any] = {}
    ok_any = False
    for horizon in horizons:
        samples = read_jsonl(processed / f"trend_dataset_{horizon}d.jsonl")
        results[f"{horizon}d"] = {}
        for feature_set in FEATURE_SETS:
            item = run_backtest_for(samples, horizon, feature_set, seed)
            results[f"{horizon}d"][feature_set] = item
            ok_any = ok_any or item.get("status") == "ok"
    return {
        "status": "ok" if ok_any else "blocked",
        "blocked_reason": "none" if ok_any else "no_successful_backtest",
        "metric_source": "5_fold_chronological_expanding_walk_forward",
        "horizons": results,
    }


def write_backtest_reports(result: dict[str, Any], config_path: str) -> None:
    cfg = load_config(config_path)
    reports = ensure_dir(resolve_path(cfg["paths"]["outputs"]) / "reports")
    write_json(reports / "backtest_results.json", result)
    lines = [
        "# Backtest Results",
        "",
        f"status: {result['status']}",
        f"blocked_reason: {result['blocked_reason']}",
        "metric_source: 5_fold_chronological_expanding_walk_forward",
        "",
        "| horizon | feature_set | successful_folds | macro_f1_mean | balanced_accuracy_mean | up_recall_mean |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for horizon, feature_sets in result["horizons"].items():
        for feature_set, item in feature_sets.items():
            if item.get("status") != "ok":
                lines.append(f"| {horizon} | {feature_set} | 0 |  |  |  |")
                continue
            lines.append(
                f"| {horizon} | {feature_set} | {item['successful_folds']} | {item['macro_f1_mean']:.4f} | "
                f"{item['balanced_accuracy_mean']:.4f} | {item['up_recall_mean']:.4f} |"
            )
    (reports / "backtest_results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run 5-fold chronological expanding walk-forward backtests.")
    parser.add_argument("--config", default="configs/config.yaml")
    parser.add_argument("--horizons", nargs="*", type=int, default=[7, 14, 21, 30])
    args = parser.parse_args()
    result = run_backtests(args.config, args.horizons)
    write_backtest_reports(result, args.config)
    print(json.dumps({"status": result["status"], "blocked_reason": result["blocked_reason"]}, ensure_ascii=False))
    return 0 if result["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
