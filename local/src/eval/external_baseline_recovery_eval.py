from __future__ import annotations

import argparse
import json
from typing import Any

import pandas as pd

from src.eval.baseline_artifact_recovery import MANIFEST_PATH
from src.eval.external_validation_common import (
    HORIZONS,
    extract_metric_summary,
    horizon_metric_table,
    load_json,
    metric_bundle,
    read_external_samples,
    sample_true_label,
    write_confusion_matrix_figure,
    write_report_pair,
)
from src.models.baseline_train import flatten_sample_features, _predict_labels, _probabilities_for_labels
from src.utils.config import resolve_path


def _load_artifact(path: str) -> dict[str, Any]:
    import joblib  # type: ignore

    payload = joblib.load(resolve_path(path))
    return payload if isinstance(payload, dict) else {"model": payload}


def _evaluate_record(record: dict[str, Any]) -> dict[str, Any]:
    horizon = int(record.get("horizon") or 0)
    samples = read_external_samples(horizon)
    if not samples:
        return {
            "status": "not_available",
            "reason": "no_external_test_samples",
            "sample_count": 0,
            "horizon": horizon,
        }
    artifact_path = str(record.get("artifact_path") or "")
    if not artifact_path:
        return {"status": "not_available", "reason": "missing_recovered_artifact_path", "sample_count": len(samples), "horizon": horizon}
    payload = _load_artifact(artifact_path)
    model = payload.get("model")
    columns = list(payload.get("columns") or [])
    feature_set = str(payload.get("feature_set") or record.get("feature_set"))
    rows = [flatten_sample_features(sample, feature_set) for sample in samples]
    frame = pd.DataFrame(rows).replace([float("inf"), float("-inf")], 0.0).fillna(0.0)
    frame = frame.reindex(columns=columns, fill_value=0.0)
    probabilities = _probabilities_for_labels(model, frame)
    y_pred = _predict_labels(model, frame, probabilities)
    y_true = [sample_true_label(sample) for sample in samples]
    metrics = metric_bundle(y_true, y_pred)
    return {
        "status": "ok",
        "horizon": horizon,
        "sample_count": len(samples),
        "metric_source": "P0f external test; recovered P0a traditional ML baseline; no external tuning",
        "feature_set": feature_set,
        "model": payload.get("model_name") or record.get("model"),
        "artifact_path": artifact_path,
        "external_test_used_for_training": False,
        "external_test_used_for_threshold_tuning": False,
        "metrics": metrics,
    }


def evaluate_external_baseline_recovery() -> dict[str, Any]:
    manifest = load_json(MANIFEST_PATH)
    if not manifest:
        result = {
            "status": "not_available",
            "blocked_reason": "none",
            "reason": "missing_baseline_recovery_manifest",
            "baseline_external_eval_status": "not_available",
            "retrained_baseline": False,
            "external_test_used_for_training": False,
        }
        write_recovery_eval_report(result)
        return result
    horizon_metrics: dict[str, Any] = {}
    y_true_all: list[str] = []
    y_pred_all: list[str] = []
    for horizon in HORIZONS:
        record = next((item for item in manifest.get("records", []) if int(item.get("horizon") or 0) == horizon and item.get("status") == "ok"), None)
        if record is None:
            horizon_metrics[f"{horizon}d"] = {"status": "not_available", "reason": "missing_recovered_artifact", "sample_count": 0}
            continue
        try:
            item = _evaluate_record(record)
        except Exception as exc:
            item = {"status": "blocked", "reason": type(exc).__name__, "error": str(exc)[:300], "sample_count": 0, "horizon": horizon}
        horizon_metrics[f"{horizon}d"] = item
        matrix = item.get("metrics", {}).get("confusion_matrix")
        if item.get("status") == "ok" and isinstance(matrix, dict):
            samples = read_external_samples(horizon)
            # Reconstruct aggregate labels from confusion matrix to avoid storing per-sample predictions.
            for truth, pred_counts in matrix.items():
                for pred, count in pred_counts.items():
                    y_true_all.extend([truth] * int(count))
                    y_pred_all.extend([pred] * int(count))
    overall_metrics = metric_bundle(y_true_all, y_pred_all) if y_true_all else {}
    ok_count = sum(1 for item in horizon_metrics.values() if item.get("status") == "ok")
    result = {
        "status": "ok" if ok_count else "not_available",
        "blocked_reason": "none",
        "baseline_external_eval_status": "ok" if ok_count else "not_available",
        "reason": "none" if ok_count else "no_recovered_artifact_evaluable",
        "metric_source": "P0f external test; recovered P0a traditional ML baseline; no external training/tuning",
        "comparison_limited": True,
        "comparison_limited_reason": "Recovered baseline may not exactly match original saved P0a artifact or best-model parameters; external test was not used for model selection.",
        "baseline_recovery_limited": manifest.get("baseline_recovery_limited", True),
        "retrained_baseline": bool(manifest.get("baseline_recovery_status") == "rebuilt"),
        "retrained_qwen_or_lora": False,
        "external_test_used_for_training": False,
        "external_test_used_for_threshold_tuning": False,
        "horizon_metrics": horizon_metrics,
        "metrics": overall_metrics,
        "summary": extract_metric_summary(overall_metrics),
        "promote_allowed": False,
    }
    if overall_metrics.get("confusion_matrix"):
        write_confusion_matrix_figure(
            overall_metrics["confusion_matrix"],
            "data/outputs/figures/external_baseline_recovery_confusion_matrix.png",
            "P0f recovered baseline external validation",
        )
    write_recovery_eval_report(result)
    return result


def write_recovery_eval_report(result: dict[str, Any]) -> None:
    lines = [
        "# External Baseline Recovery Evaluation",
        "",
        f"status: {result['status']}",
        f"blocked_reason: {result['blocked_reason']}",
        f"baseline_external_eval_status: {result['baseline_external_eval_status']}",
        f"metric_source: {result.get('metric_source', 'not_available')}",
        f"comparison_limited: {str(result.get('comparison_limited', True)).lower()}",
        "external_test_used_for_training: false",
        "external_test_used_for_threshold_tuning: false",
        "promote_allowed: false",
        "",
        "## Horizon Metrics",
        "",
        *horizon_metric_table(result.get("horizon_metrics", {})),
        "",
        "## JSON",
        "```json",
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True),
        "```",
    ]
    write_report_pair("external_baseline_recovery_eval", result, lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate recovered P0a baseline on P0e external test.")
    parser.parse_args()
    result = evaluate_external_baseline_recovery()
    print(json.dumps({"status": result["status"], "blocked_reason": result["blocked_reason"]}, ensure_ascii=False))
    return 0 if result["status"] in {"ok", "not_available"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
