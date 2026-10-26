from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from src.eval.metrics import LABEL_ORDER, LABEL_STABLE, LABEL_UP
from src.eval.p0c4a_common import load_json, map_sft_to_trend, metric_bundle, qwen_samples
from src.utils.config import resolve_path
from src.utils.io import ensure_dir, read_jsonl, write_json, write_jsonl


PREDICTIONS_PATH = "data/outputs/reports/qwen_guard_ensemble_predictions.jsonl"
BASELINE_PREDICTIONS_PATH = "data/outputs/reports/same_split_baseline_predictions.jsonl"


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _baseline_predictions(split: str) -> list[dict[str, Any]]:
    return [row for row in read_jsonl(resolve_path(BASELINE_PREDICTIONS_PATH)) if row.get("split") == split]


def _qwen_prob(sample: dict[str, Any]) -> dict[str, float]:
    pred = str(sample.get("predicted_label"))
    conf = min(1.0, max(0.0, _as_float(sample.get("confidence"), 0.8)))
    rest = (1.0 - conf) / max(len(LABEL_ORDER) - 1, 1)
    return {label: (conf if label == pred else rest) for label in LABEL_ORDER}


def _up_prob(row: dict[str, Any]) -> float:
    probs = row.get("probabilities") if isinstance(row.get("probabilities"), dict) else {}
    return _as_float(probs.get(LABEL_UP), 0.0)


def _false_alarm_rate(y_true: list[str], flags: list[bool]) -> float:
    non_up = sum(1 for label in y_true if label != LABEL_UP)
    false_flags = sum(1 for label, flag in zip(y_true, flags) if flag and label != LABEL_UP)
    return false_flags / non_up if non_up else 0.0


def _flag_metrics(y_true: list[str], flags: list[bool]) -> dict[str, Any]:
    true_up = sum(1 for label in y_true if label == LABEL_UP)
    flagged = sum(1 for flag in flags if flag)
    true_flagged = sum(1 for label, flag in zip(y_true, flags) if flag and label == LABEL_UP)
    return {
        "review_recall": true_flagged / true_up if true_up else 0.0,
        "review_precision": true_flagged / flagged if flagged else 0.0,
        "review_burden": flagged / len(flags) if flags else 0.0,
        "flagged_count": flagged,
    }


def _strategy_metrics(name: str, y_true: list[str], y_pred: list[str], qwen_metrics: dict[str, Any]) -> dict[str, Any]:
    metrics = metric_bundle(y_true, y_pred, qwen_metrics)
    metrics["strategy"] = name
    return metrics


def _tune_baseline_threshold(val_rows: list[dict[str, Any]]) -> dict[str, Any]:
    y_true = [str(row.get("true_label")) for row in val_rows]
    best = {"threshold": 0.35, "score": -999.0}
    for threshold in [0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.50, 0.60]:
        flags = [_up_prob(row) >= threshold for row in val_rows]
        fm = _flag_metrics(y_true, flags)
        score = fm["review_recall"] + 0.15 * fm["review_precision"] - 0.25 * _false_alarm_rate(y_true, flags)
        if score > best["score"]:
            best = {"threshold": threshold, "score": score, **fm, "false_alarm_rate": _false_alarm_rate(y_true, flags)}
    return best


def _risk_score_from_features(row: dict[str, Any]) -> dict[str, float]:
    features = row.get("features") or row.get("input_features") or {}
    case = features.get("case_features") or {}
    growth = max(_as_float(case.get("growth_7d")), _as_float(case.get("growth_14d")), _as_float(case.get("growth_28d")))
    acceleration = _as_float(case.get("case_acceleration"))
    recent = max(_as_float(case.get("new_cases_7d_sum")), _as_float(case.get("new_cases_14d_sum")))
    return {"growth": growth, "case_acceleration": acceleration, "recent_cases": recent}


def _risk_flag(row: dict[str, Any], params: dict[str, float]) -> bool:
    score = _risk_score_from_features(row)
    return (
        score["growth"] >= params["growth_threshold"]
        and score["recent_cases"] >= params["recent_cases_threshold"]
    ) or (
        score["case_acceleration"] >= params["acceleration_threshold"]
        and score["recent_cases"] >= params["recent_cases_threshold"]
    )


def _tune_case_guard(val_rows: list[dict[str, Any]]) -> dict[str, Any]:
    y_true = [str(row.get("label")) for row in val_rows]
    best: dict[str, Any] = {"score": -999.0, "growth_threshold": 0.2, "acceleration_threshold": 0.0, "recent_cases_threshold": 1.0}
    for growth in [0.1, 0.2, 0.3, 0.5, 1.0]:
        for acceleration in [0.0, 0.1, 0.25, 0.5]:
            for recent in [0.0, 1.0, 5.0, 10.0]:
                params = {"growth_threshold": growth, "acceleration_threshold": acceleration, "recent_cases_threshold": recent}
                flags = [_risk_flag(row, params) for row in val_rows]
                fm = _flag_metrics(y_true, flags)
                score = fm["review_recall"] + 0.15 * fm["review_precision"] - 0.25 * _false_alarm_rate(y_true, flags)
                if score > best["score"]:
                    best = {**params, "score": score, **fm, "false_alarm_rate": _false_alarm_rate(y_true, flags)}
    return best


def _case_row_for_sample(sample: dict[str, Any], mapped_test: list[dict[str, Any]]) -> dict[str, Any]:
    idx = int(str(sample.get("sample_id", "0")).split("_")[-1])
    return mapped_test[idx] if 0 <= idx < len(mapped_test) else {"features": sample.get("input_features", {}), "label": sample.get("true_label")}


def evaluate_strategies() -> dict[str, Any]:
    try:
        samples = qwen_samples(required=True)
    except FileNotFoundError as exc:
        return {"status": "blocked", "blocked_reason": str(exc), "missing_artifacts": ["qwen_lora_eval_samples.jsonl"]}
    baseline_report = load_json("data/outputs/reports/same_split_baseline_eval.json")
    baseline_available = bool(baseline_report.get("same_split_baseline_available"))
    baseline_test = {str(row.get("sample_id")): row for row in _baseline_predictions("test")}
    baseline_val = _baseline_predictions("val")
    mapped, _coverage = map_sft_to_trend()
    mapped_test = mapped.get("test", [])
    mapped_val = mapped.get("val", [])

    y_true = [str(row.get("true_label")) for row in samples]
    qwen_pred = [str(row.get("predicted_label")) for row in samples]
    qwen_metrics = metric_bundle(y_true, qwen_pred)
    strategy_results: dict[str, Any] = {
        "qwen_only": {
            "status": "ok",
            "metrics": qwen_metrics,
            "metric_source": "P0c-3 Qwen test predictions",
        }
    }
    output_rows: list[dict[str, Any]] = []

    if baseline_available and baseline_test:
        baseline_pred = [str(baseline_test.get(str(row.get("sample_id")), {}).get("predicted_label", row.get("predicted_label"))) for row in samples]
        strategy_results["baseline_only"] = {"status": "ok", "metrics": _strategy_metrics("baseline_only", y_true, baseline_pred, qwen_metrics)}
        baseline_threshold = _tune_baseline_threshold(baseline_val)
        guard_pred: list[str] = []
        guard_flags: list[bool] = []
        for sample in samples:
            base = baseline_test.get(str(sample.get("sample_id")), {})
            flag = str(sample.get("predicted_label")) == LABEL_STABLE and _up_prob(base) >= baseline_threshold["threshold"]
            guard_flags.append(flag)
            guard_pred.append(LABEL_UP if flag else str(sample.get("predicted_label")))
        strategy_results["baseline_up_risk_guard"] = {
            "status": "ok",
            "threshold_tuning_source": "SFT validation split only",
            "frozen_threshold": baseline_threshold,
            "metrics": _strategy_metrics("baseline_up_risk_guard", y_true, guard_pred, qwen_metrics),
        }
    else:
        strategy_results["baseline_only"] = {"status": "not_available", "reason": "same_split_baseline_not_available"}
        strategy_results["baseline_up_risk_guard"] = {"status": "not_available", "reason": "same_split_baseline_not_available"}
        baseline_pred = list(qwen_pred)
        guard_pred = list(qwen_pred)
        guard_flags = [False for _ in samples]

    case_params = _tune_case_guard(mapped_val)
    case_flags = [str(sample.get("predicted_label")) == LABEL_STABLE and _risk_flag(_case_row_for_sample(sample, mapped_test), case_params) for sample in samples]
    case_tri_pred = [LABEL_UP if flag else str(sample.get("predicted_label")) for sample, flag in zip(samples, case_flags)]
    strategy_results["case_growth_safety_guard"] = {
        "status": "ok",
        "threshold_tuning_source": "SFT validation split only",
        "frozen_thresholds": case_params,
        "tri_class_mode": {
            "label_mapping": "risk_review_required mapped to 上升",
            "metrics": _strategy_metrics("case_growth_safety_guard_tri_class", y_true, case_tri_pred, qwen_metrics),
        },
        "review_mode": {
            "label_mapping": "risk_review_required counted as review flag; original Qwen trend preserved",
            **_flag_metrics(y_true, case_flags),
        },
    }

    if baseline_available and baseline_test:
        ensemble_pred = []
        for sample in samples:
            base = baseline_test.get(str(sample.get("sample_id")), {})
            base_probs = base.get("probabilities") if isinstance(base.get("probabilities"), dict) else {}
            qprobs = _qwen_prob(sample)
            fused = {label: 0.5 * qprobs.get(label, 0.0) + 0.5 * _as_float(base_probs.get(label), 0.0) for label in LABEL_ORDER}
            ensemble_pred.append(max(fused.items(), key=lambda item: item[1])[0])
        strategy_results["qwen_baseline_ensemble"] = {
            "status": "ok",
            "fusion": "fixed 0.5 qwen pseudo-probability + 0.5 baseline probability; no training; no test tuning",
            "metrics": _strategy_metrics("qwen_baseline_ensemble", y_true, ensemble_pred, qwen_metrics),
        }
    else:
        ensemble_pred = list(qwen_pred)
        strategy_results["qwen_baseline_ensemble"] = {"status": "not_available", "reason": "same_split_baseline_not_available"}

    for idx, sample in enumerate(samples):
        output_rows.append(
            {
                "sample_id": sample.get("sample_id"),
                "true_label": sample.get("true_label"),
                "qwen_only": qwen_pred[idx],
                "baseline_only": baseline_pred[idx] if baseline_available else None,
                "baseline_up_risk_guard": guard_pred[idx] if baseline_available else None,
                "case_growth_safety_guard_tri_class": case_tri_pred[idx],
                "case_growth_safety_guard_review_flag": bool(case_flags[idx]),
                "qwen_baseline_ensemble": ensemble_pred[idx] if baseline_available else None,
            }
        )
    write_jsonl(resolve_path(PREDICTIONS_PATH), output_rows)
    best = choose_best_strategy(strategy_results, qwen_metrics)
    result = {
        "status": "ok",
        "blocked_reason": "none",
        "qwen_original_predictions_preserved": True,
        "predictions_path": PREDICTIONS_PATH,
        "qwen_only_metrics": qwen_metrics,
        "strategies": strategy_results,
        "best_non_training_strategy": best,
        "recommendation_inputs": {
            "up_recall_target": 0.50,
            "max_macro_f1_drop": 0.03,
            "min_up_precision": 0.30,
            "max_false_alarm_rate": 0.15,
        },
    }
    result["recommended_next_action"] = recommended_next_action(result)
    return result


def choose_best_strategy(strategies: dict[str, Any], qwen_metrics: dict[str, Any]) -> dict[str, Any]:
    best_name = "qwen_only"
    best_metrics = qwen_metrics
    for name, payload in strategies.items():
        metrics = payload.get("metrics") or payload.get("tri_class_mode", {}).get("metrics")
        if not isinstance(metrics, dict):
            continue
        if (metrics.get("up_recall", 0.0), metrics.get("macro_f1", 0.0)) > (best_metrics.get("up_recall", 0.0), best_metrics.get("macro_f1", 0.0)):
            best_name = name
            best_metrics = metrics
    return {"strategy": best_name, "metrics": best_metrics}


def recommended_next_action(result: dict[str, Any]) -> str:
    qwen = result["qwen_only_metrics"]
    best = result["best_non_training_strategy"]
    metrics = best["metrics"]
    baseline = result["strategies"].get("baseline_only", {})
    baseline_metrics = baseline.get("metrics") if isinstance(baseline.get("metrics"), dict) else None
    if baseline_metrics and baseline_metrics.get("up_recall", 0) >= qwen.get("up_recall", 0) + 0.10 and baseline_metrics.get("macro_f1", 0) >= qwen.get("macro_f1", 0) + 0.03:
        return "keep_baseline_as_primary_and_qwen_as_explanation_agent"
    strong = (
        metrics.get("up_recall", 0.0) >= 0.50
        and metrics.get("macro_f1", 0.0) >= qwen.get("macro_f1", 0.0) - 0.03
        and metrics.get("up_precision", 0.0) >= 0.30
        and metrics.get("false_alarm_rate", 1.0) <= 0.15
    )
    if strong and best["strategy"] != "qwen_only":
        return "evaluate_guarded_candidate_before_retraining"
    if metrics.get("up_recall", 0.0) > qwen.get("up_recall", 0.0) + 0.05:
        return "prepare_candidate_v2_training_with_up_recall_focus"
    return "prepare_candidate_v2_training_with_up_recall_focus"


def write_figure(result: dict[str, Any], path: Path) -> None:
    ensure_dir(path.parent)
    if result.get("status") != "ok":
        path.with_suffix(".txt").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        names = []
        recalls = []
        macros = []
        false_alarms = []
        for name, payload in result["strategies"].items():
            metrics = payload.get("metrics") or payload.get("tri_class_mode", {}).get("metrics")
            if isinstance(metrics, dict):
                names.append(name)
                recalls.append(metrics.get("up_recall", 0.0))
                macros.append(metrics.get("macro_f1", 0.0))
                false_alarms.append(metrics.get("false_alarm_rate", 0.0))
        fig, ax = plt.subplots(figsize=(9, 4.5))
        x = range(len(names))
        ax.bar([i - 0.25 for i in x], recalls, width=0.25, label="up recall")
        ax.bar(x, macros, width=0.25, label="macro-F1")
        ax.bar([i + 0.25 for i in x], false_alarms, width=0.25, label="false alarm rate")
        ax.set_xticks(list(x), names, rotation=25, ha="right")
        ax.legend()
        ax.set_ylim(0, 1)
        fig.tight_layout()
        fig.savefig(path, dpi=150)
        plt.close(fig)
    except Exception:
        path.with_suffix(".txt").write_text(json.dumps(result.get("best_non_training_strategy", {}), ensure_ascii=False, indent=2), encoding="utf-8")


def write_report(result: dict[str, Any]) -> Path:
    reports = ensure_dir(resolve_path("data/outputs/reports"))
    figure = resolve_path("data/outputs/figures/qwen_guard_ensemble_comparison.png")
    write_figure(result, figure)
    result["figure"] = "data/outputs/figures/qwen_guard_ensemble_comparison.png"
    write_json(reports / "qwen_guard_ensemble_eval.json", result)
    best = result.get("best_non_training_strategy", {})
    lines = [
        "# Qwen Guard Ensemble Eval",
        "",
        f"status: {result['status']}",
        f"blocked_reason: {result['blocked_reason']}",
        f"best_non_training_strategy: {best.get('strategy')}",
        f"recommended_next_action: {result.get('recommended_next_action')}",
        "",
        "| strategy | status | up recall | up precision | macro-F1 | false alarm rate | stable->up |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for name, payload in result.get("strategies", {}).items():
        metrics = payload.get("metrics") or payload.get("tri_class_mode", {}).get("metrics")
        if not isinstance(metrics, dict):
            lines.append(f"| {name} | {payload.get('status')} |  |  |  |  |  |")
            continue
        lines.append(
            f"| {name} | {payload.get('status')} | {metrics.get('up_recall', 0):.4f} | {metrics.get('up_precision', 0):.4f} | "
            f"{metrics.get('macro_f1', 0):.4f} | {metrics.get('false_alarm_rate', 0):.4f} | {metrics.get('stable_to_up_count', 0)} |"
        )
    lines.extend(["", "## Full Result", "```json", json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), "```"])
    path = reports / "qwen_guard_ensemble_eval.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate non-training Qwen guard and ensemble strategies.")
    parser.parse_args()
    result = evaluate_strategies()
    report = write_report(result)
    print(json.dumps({"status": result["status"], "blocked_reason": result["blocked_reason"], "report": report.as_posix()}, ensure_ascii=False))
    return 0 if result["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
