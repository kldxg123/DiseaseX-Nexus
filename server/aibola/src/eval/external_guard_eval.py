from __future__ import annotations

import argparse
import json
from typing import Any

from src.eval.external_validation_common import HORIZONS, load_json, metric_bundle, read_external_samples, sample_true_label, write_report_pair
from src.eval.metrics import LABEL_STABLE, LABEL_UP


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def load_frozen_thresholds() -> dict[str, Any]:
    report = load_json("data/outputs/reports/qwen_guard_ensemble_eval.json")
    strategy = report.get("strategies", {}).get("case_growth_safety_guard", {})
    thresholds = strategy.get("frozen_thresholds")
    if isinstance(thresholds, dict) and thresholds:
        return thresholds
    return {}


def _case_features(sample: dict[str, Any]) -> dict[str, Any]:
    features = sample.get("features") if isinstance(sample.get("features"), dict) else {}
    if isinstance(features.get("case_features"), dict):
        return features["case_features"]
    return sample.get("case_features", {}) if isinstance(sample.get("case_features"), dict) else {}


def risk_flag(sample: dict[str, Any], params: dict[str, Any]) -> bool:
    case = _case_features(sample)
    growth = max(_as_float(case.get("growth_7d")), _as_float(case.get("growth_14d")), _as_float(case.get("growth_28d")))
    acceleration = _as_float(case.get("case_acceleration"))
    recent = max(_as_float(case.get("new_cases_7d_sum")), _as_float(case.get("new_cases_14d_sum")))
    return (
        growth >= _as_float(params.get("growth_threshold"), 0.2)
        and recent >= _as_float(params.get("recent_cases_threshold"), 1.0)
    ) or (
        acceleration >= _as_float(params.get("acceleration_threshold"), 0.0)
        and recent >= _as_float(params.get("recent_cases_threshold"), 1.0)
    )


def review_metrics(y_true: list[str], flags: list[bool]) -> dict[str, Any]:
    true_up = sum(1 for label in y_true if label == LABEL_UP)
    flagged = sum(1 for flag in flags if flag)
    true_flagged = sum(1 for label, flag in zip(y_true, flags) if flag and label == LABEL_UP)
    return {
        "review_recall": true_flagged / true_up if true_up else 0.0,
        "review_precision": true_flagged / flagged if flagged else 0.0,
        "review_burden": flagged / len(flags) if flags else 0.0,
        "flagged_count": flagged,
    }


def evaluate_guard() -> dict[str, Any]:
    thresholds = load_frozen_thresholds()
    if not thresholds:
        result = {
            "status": "not_available",
            "blocked_reason": "none",
            "reason": "missing_p0c4a_case_growth_safety_guard_thresholds",
            "threshold_source": "P0c-4A validation tuning",
            "thresholds_tuned_on_external_test": False,
            "horizons": {},
        }
        write_report(result)
        return result
    result: dict[str, Any] = {
        "status": "ok",
        "blocked_reason": "none",
        "threshold_source": "P0c-4A validation tuning",
        "frozen_thresholds": thresholds,
        "thresholds_tuned_on_external_test": False,
        "strategy": "case_growth_safety_guard",
        "horizons": {},
    }
    for horizon in HORIZONS:
        samples = read_external_samples(horizon)
        key = f"{horizon}d"
        if horizon == 30 and not samples:
            result["horizons"][key] = {"status": "not_available", "reason": "no_external_test_samples", "sample_count": 0}
            continue
        y_true = [sample_true_label(sample) for sample in samples]
        flags = [risk_flag(sample, thresholds) for sample in samples]
        tri_pred = [LABEL_UP if flag else LABEL_STABLE for flag in flags]
        tri_metrics = metric_bundle(y_true, tri_pred)
        result["horizons"][key] = {
            "status": "ok",
            "sample_count": len(samples),
            "low_n_warning": "low_n_warning" if horizon == 21 and samples else None,
            "tri_class_mode": {
                "label_mapping": "risk_review_required mapped to 上升; otherwise 稳定",
                "metrics": tri_metrics,
            },
            "review_mode": {
                "label_mapping": "risk_review_required counted as review flag; no direct trend overwrite",
                **review_metrics(y_true, flags),
            },
        }
    write_report(result)
    return result


def write_report(result: dict[str, Any]) -> None:
    lines = [
        "# External Guard Eval",
        "",
        f"status: {result['status']}",
        f"blocked_reason: {result.get('blocked_reason', 'none')}",
        f"threshold_source: {result.get('threshold_source')}",
        "thresholds_tuned_on_external_test: false",
        "",
        "| horizon | samples | up recall | up precision | macro-F1 | false alarm rate | review recall | review precision | review burden | warning |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for horizon in [f"{h}d" for h in HORIZONS]:
        item = result.get("horizons", {}).get(horizon, {})
        if item.get("status") == "not_available":
            lines.append(f"| {horizon} | 0 |  |  |  |  |  |  |  | {item.get('reason')} |")
            continue
        metrics = item.get("tri_class_mode", {}).get("metrics", {})
        review = item.get("review_mode", {})
        lines.append(
            f"| {horizon} | {item.get('sample_count', 0)} | {metrics.get('up_recall', 0):.4f} | "
            f"{metrics.get('up_precision', 0):.4f} | {metrics.get('macro_f1', 0):.4f} | "
            f"{metrics.get('false_alarm_rate', 0):.4f} | {review.get('review_recall', 0):.4f} | "
            f"{review.get('review_precision', 0):.4f} | {review.get('review_burden', 0):.4f} | {item.get('low_n_warning') or ''} |"
        )
    lines.extend(["", "## JSON", "```json", json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), "```"])
    write_report_pair("external_guard_eval", result, lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate frozen P0c-4A case-growth guard on P0e external test.")
    parser.parse_args()
    result = evaluate_guard()
    print(json.dumps({"status": result["status"], "blocked_reason": result.get("blocked_reason", "none")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
