from __future__ import annotations

import argparse
import json
from typing import Any

from src.eval.metrics import LABEL_STABLE, LABEL_UP, classification_metrics
from src.utils.config import load_config, resolve_path
from src.utils.io import ensure_dir, read_jsonl, write_json, write_jsonl


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _flatten_features(sample: dict[str, Any]) -> dict[str, float]:
    features = sample.get("features") if isinstance(sample.get("features"), dict) else {}
    output: dict[str, float] = {}
    for group in ["case_features", "mobility_features", "weather_features", "ecology_features", "intervention_features", "external_features"]:
        values = features.get(group) if isinstance(features.get(group), dict) else {}
        for key, value in values.items():
            output[key] = _as_float(value)
    return output


def chronological_splits(samples: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    ordered = sorted(samples, key=lambda row: (str(row.get("date", "")), str(row.get("sample_id", ""))))
    if len(ordered) < 20:
        return ordered, [], []
    train_end = max(1, int(len(ordered) * 0.6))
    val_end = max(train_end + 1, int(len(ordered) * 0.8))
    return ordered[:train_end], ordered[train_end:val_end], ordered[val_end:]


def risk_flag(sample: dict[str, Any], params: dict[str, float]) -> bool:
    f = _flatten_features(sample)
    growth = max(_as_float(f.get("growth_7d")), _as_float(f.get("growth_14d")), _as_float(f.get("case_growth_x_geo")))
    road_growth = _as_float(f.get("case_growth_x_road_access"))
    incidence = max(_as_float(f.get("population_normalized_incidence_7d")), _as_float(f.get("population_normalized_incidence_14d")))
    rainfall = _as_float(f.get("rainfall_lag_7"))
    access = max(_as_float(f.get("cross_border_accessibility_index")), _as_float(f.get("road_density_km_per_area")) / 10.0)
    recent_cases = max(_as_float(f.get("new_cases_7d_sum")), _as_float(f.get("new_cases_14d_sum")))
    return (
        growth >= params["growth_threshold"] and incidence >= params["incidence_threshold"] and recent_cases >= params["recent_cases_threshold"]
    ) or (
        road_growth >= params["road_growth_threshold"] and access >= params["access_threshold"] and recent_cases >= params["recent_cases_threshold"]
    ) or (
        rainfall >= params["rainfall_threshold"] and growth >= params["rainfall_growth_threshold"] and recent_cases >= params["recent_cases_threshold"]
    )


def _false_alarm_rate(y_true: list[str], y_pred: list[str]) -> float:
    non_up = sum(1 for label in y_true if label != LABEL_UP)
    false_up = sum(1 for truth, pred in zip(y_true, y_pred) if truth != LABEL_UP and pred == LABEL_UP)
    return false_up / non_up if non_up else 0.0


def _stable_to_up_count(y_true: list[str], y_pred: list[str]) -> int:
    return sum(1 for truth, pred in zip(y_true, y_pred) if truth == LABEL_STABLE and pred == LABEL_UP)


def apply_guard(samples: list[dict[str, Any]], params: dict[str, float]) -> list[str]:
    return [LABEL_UP if risk_flag(sample, params) else LABEL_STABLE for sample in samples]


def score_params(samples: list[dict[str, Any]], params: dict[str, float]) -> dict[str, Any]:
    y_true = [str(sample.get("label")) for sample in samples]
    y_pred = apply_guard(samples, params)
    metrics = classification_metrics(y_true, y_pred)
    metrics["false_alarm_rate"] = _false_alarm_rate(y_true, y_pred)
    metrics["stable_to_up_count"] = _stable_to_up_count(y_true, y_pred)
    metrics["up_recall"] = metrics["per_class"][LABEL_UP]["recall"]
    metrics["up_precision"] = metrics["per_class"][LABEL_UP]["precision"]
    return metrics


def tune_on_validation(val: list[dict[str, Any]]) -> dict[str, Any]:
    best: dict[str, Any] = {"score": -999.0}
    grids = {
        "growth_threshold": [0.1, 0.2, 0.3, 0.5, 1.0],
        "incidence_threshold": [0.0, 0.1, 0.5, 1.0],
        "recent_cases_threshold": [0.0, 1.0, 5.0, 10.0],
        "road_growth_threshold": [0.1, 0.2, 0.5, 1.0],
        "access_threshold": [0.2, 0.5, 1.0],
        "rainfall_threshold": [0.0, 5.0, 20.0, 50.0],
        "rainfall_growth_threshold": [0.1, 0.2, 0.5],
    }
    for growth in grids["growth_threshold"]:
        for incidence in grids["incidence_threshold"]:
            for recent in grids["recent_cases_threshold"]:
                for road_growth in grids["road_growth_threshold"]:
                    for access in grids["access_threshold"]:
                        for rainfall in grids["rainfall_threshold"]:
                            for rainfall_growth in grids["rainfall_growth_threshold"]:
                                params = {
                                    "growth_threshold": growth,
                                    "incidence_threshold": incidence,
                                    "recent_cases_threshold": recent,
                                    "road_growth_threshold": road_growth,
                                    "access_threshold": access,
                                    "rainfall_threshold": rainfall,
                                    "rainfall_growth_threshold": rainfall_growth,
                                }
                                metrics = score_params(val, params)
                                score = metrics["up_recall"] + 0.20 * metrics["up_precision"] + 0.10 * metrics["macro_f1"] - 0.35 * metrics["false_alarm_rate"]
                                if score > best["score"]:
                                    best = {"score": score, "params": params, "validation_metrics": metrics}
    return best


def evaluate_guard_after_data_expansion(config_path: str = "configs/config.yaml") -> dict[str, Any]:
    cfg = load_config(config_path)
    processed = resolve_path(cfg["paths"]["data_processed"])
    reports = ensure_dir(resolve_path(cfg["paths"]["outputs"]) / "reports")
    samples = read_jsonl(processed / "trend_dataset_7d.jsonl")
    train, val, test = chronological_splits(samples)
    if not val or not test:
        result = {"status": "blocked", "blocked_reason": "not_enough_samples_for_chronological_val_test", "samples": len(samples)}
    else:
        best = tune_on_validation(val)
        test_pred = apply_guard(test, best["params"])
        test_true = [str(sample.get("label")) for sample in test]
        test_metrics = classification_metrics(test_true, test_pred)
        test_metrics["false_alarm_rate"] = _false_alarm_rate(test_true, test_pred)
        test_metrics["stable_to_up_count"] = _stable_to_up_count(test_true, test_pred)
        test_metrics["up_recall"] = test_metrics["per_class"][LABEL_UP]["recall"]
        test_metrics["up_precision"] = test_metrics["per_class"][LABEL_UP]["precision"]
        predictions = [
            {
                "sample_id": sample.get("sample_id"),
                "date": sample.get("date"),
                "true_label": sample.get("label"),
                "geo_access_case_growth_guard": pred,
                "risk_review_required": pred == LABEL_UP,
            }
            for sample, pred in zip(test, test_pred)
        ]
        write_jsonl(reports / "guard_after_data_expansion_predictions.jsonl", predictions)
        result = {
            "status": "ok",
            "blocked_reason": "none",
            "strategy": "geo_access_case_growth_guard",
            "threshold_tuning_source": "chronological validation split only",
            "test_evaluation_source": "chronological held-out test split",
            "split_counts": {"train": len(train), "validation": len(val), "test": len(test)},
            "frozen_thresholds": best["params"],
            "validation_metrics": best["validation_metrics"],
            "test_metrics": test_metrics,
            "predictions_path": "data/outputs/reports/guard_after_data_expansion_predictions.jsonl",
            "promote_allowed": False,
            "qwen_or_lora_used": False,
        }
    write_json(reports / "guard_after_data_expansion.json", result)
    lines = [
        "# Guard After Data Expansion",
        "",
        f"status: {result['status']}",
        f"blocked_reason: {result['blocked_reason']}",
        "qwen_or_lora_used: false",
        "promote_allowed: false",
    ]
    if result["status"] == "ok":
        metrics = result["test_metrics"]
        lines.extend(
            [
                f"strategy: {result['strategy']}",
                f"threshold_tuning_source: {result['threshold_tuning_source']}",
                f"test_up_recall: {metrics['up_recall']:.4f}",
                f"test_up_precision: {metrics['up_precision']:.4f}",
                f"test_macro_f1: {metrics['macro_f1']:.4f}",
                f"test_balanced_accuracy: {metrics['balanced_accuracy']:.4f}",
                f"test_false_alarm_rate: {metrics['false_alarm_rate']:.4f}",
                f"stable_to_up_count: {metrics['stable_to_up_count']}",
            ]
        )
    (reports / "guard_after_data_expansion.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate P0d non-training geo/access/case-growth guard.")
    parser.add_argument("--config", default="configs/config.yaml")
    args = parser.parse_args()
    result = evaluate_guard_after_data_expansion(args.config)
    print(json.dumps({"status": result["status"], "blocked_reason": result["blocked_reason"]}, ensure_ascii=False))
    return 0 if result["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
