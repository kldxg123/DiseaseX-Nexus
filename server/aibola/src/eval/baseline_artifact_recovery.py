from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np

from src.eval.external_baseline_eval import ARTIFACT_PATTERNS
from src.models.baseline_train import (
    FEATURE_SETS,
    LABEL_TO_INT,
    chronological_holdout,
    evaluate_predictions,
    fit_first_available,
    samples_to_frame,
    _predict_labels,
    _probabilities_for_labels,
)
from src.utils.config import load_config, resolve_path
from src.utils.io import ensure_dir, ensure_parent, read_jsonl, write_json


HORIZONS = [7, 14, 21, 30]
REPORT_NAME = "baseline_artifact_recovery_report"
MANIFEST_PATH = "data/processed/baseline_artifacts/recovered_p0f/baseline_artifact_recovery_manifest.json"


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _find_existing_artifacts() -> list[str]:
    found: list[str] = []
    for pattern in ARTIFACT_PATTERNS:
        found.extend(path.as_posix() for path in resolve_path(".").glob(pattern))
    return sorted(set(found))


def _select_feature_set(horizon: int, baseline_report: dict[str, Any]) -> tuple[str, str]:
    horizon_rows = baseline_report.get("horizons", {}).get(f"{horizon}d", {})
    if isinstance(horizon_rows, dict):
        ok_rows = [
            (feature_set, item)
            for feature_set, item in horizon_rows.items()
            if isinstance(item, dict) and item.get("status") == "ok"
        ]
        if ok_rows:
            feature_set, _ = max(
                ok_rows,
                key=lambda pair: pair[1].get("metrics", {}).get("macro_f1", -1.0),
            )
            return feature_set, "selected_from_p0a_baseline_results"
    return "cases_weather_mobility_proxy", "fallback_feature_set_no_p0a_per_horizon_selection"


def _train_recovered_artifact(
    samples: list[dict[str, Any]],
    horizon: int,
    feature_set: str,
    seed: int,
    output_dir: Path,
) -> dict[str, Any]:
    if len(samples) < 10:
        return {
            "status": "not_available",
            "horizon": horizon,
            "feature_set": feature_set,
            "reason": "not_enough_internal_p0a_samples",
            "sample_count": len(samples),
        }
    train, holdout = chronological_holdout(samples)
    x_train, columns = samples_to_frame(train, feature_set)
    x_holdout, _ = samples_to_frame(holdout, feature_set)
    x_holdout = x_holdout.reindex(columns=columns, fill_value=0.0)
    y_train = np.array([LABEL_TO_INT[str(sample["label"])] for sample in train], dtype=int)
    y_true = [str(sample["label"]) for sample in holdout]
    model, model_name, attempts = fit_first_available(x_train, y_train, seed=seed)
    if model is None or model_name is None:
        return {
            "status": "blocked",
            "horizon": horizon,
            "feature_set": feature_set,
            "blocked_reason": "all_real_ml_baselines_failed",
            "fallback_attempts": attempts,
            "train_samples": len(train),
            "holdout_samples": len(holdout),
        }
    probabilities = _probabilities_for_labels(model, x_holdout)
    y_pred = _predict_labels(model, x_holdout, probabilities)
    metrics = evaluate_predictions(y_true, y_pred, probabilities)
    try:
        import joblib  # type: ignore
    except Exception as exc:
        return {
            "status": "blocked",
            "horizon": horizon,
            "feature_set": feature_set,
            "blocked_reason": "joblib_not_available",
            "error": str(exc)[:200],
        }
    artifact_path = output_dir / f"p0f_recovered_baseline_{horizon}d_{feature_set}.joblib"
    joblib.dump(
        {
            "model": model,
            "model_name": model_name,
            "feature_set": feature_set,
            "columns": columns,
            "horizon": horizon,
            "label_to_int": LABEL_TO_INT,
            "training_source": "P0a internal chronological 70/30 train split only",
            "external_test_used_for_training": False,
            "seed": seed,
        },
        artifact_path,
    )
    return {
        "status": "ok",
        "horizon": horizon,
        "feature_set": feature_set,
        "model": model_name,
        "artifact_path": artifact_path.as_posix(),
        "fallback_attempts": attempts,
        "train_samples": len(train),
        "holdout_samples": len(holdout),
        "holdout_metric_source": "P0a internal chronological 70/30 holdout; not external validation",
        "holdout_metrics": metrics,
    }


def recover_baseline_artifacts(config_path: str = "configs/config.yaml") -> dict[str, Any]:
    cfg = load_config(config_path)
    processed = resolve_path(cfg["paths"]["data_processed"])
    reports = ensure_dir(resolve_path(cfg["paths"]["outputs"]) / "reports")
    output_dir = ensure_dir(resolve_path("data/processed/baseline_artifacts/recovered_p0f"))
    seed = int(cfg.get("project", {}).get("random_seed", 42))
    existing = _find_existing_artifacts()
    baseline_report = _load_json(reports / "baseline_results.json")
    records: list[dict[str, Any]] = []
    limited_reasons: list[str] = []
    if not existing:
        limited_reasons.append("no_saved_p0a_baseline_artifact_found_rebuilt_from_internal_p0a_train_split")
    for horizon in HORIZONS:
        feature_set, feature_reason = _select_feature_set(horizon, baseline_report)
        if feature_reason.startswith("fallback"):
            limited_reasons.append(f"{horizon}d:{feature_reason}")
        dataset_path = processed / f"trend_dataset_{horizon}d.jsonl"
        samples = read_jsonl(dataset_path) if dataset_path.exists() else []
        record = _train_recovered_artifact(samples, horizon, feature_set, seed, output_dir)
        record["feature_selection_reason"] = feature_reason
        record["source_dataset"] = dataset_path.as_posix()
        records.append(record)
    blocked = [item for item in records if item.get("status") == "blocked"]
    ok_records = [item for item in records if item.get("status") == "ok"]
    result = {
        "status": "blocked" if blocked and not ok_records else "ok",
        "blocked_reason": "none" if not (blocked and not ok_records) else "all_horizon_recovery_failed",
        "baseline_recovery_status": "rebuilt" if ok_records else "not_available",
        "baseline_recovery_limited": bool(limited_reasons),
        "baseline_recovery_limited_reasons": sorted(set(limited_reasons)),
        "saved_artifacts_found": existing,
        "retrained_qwen_or_lora": False,
        "external_test_used_for_training": False,
        "external_test_used_for_threshold_tuning": False,
        "training_source": "P0a internal trend_dataset_{7,14,21,30}d.jsonl only",
        "records": records,
        "manifest_path": MANIFEST_PATH,
    }
    ensure_parent(resolve_path(MANIFEST_PATH))
    write_json(resolve_path(MANIFEST_PATH), result)
    write_recovery_report(result)
    return result


def write_recovery_report(result: dict[str, Any]) -> None:
    lines = [
        "# Baseline Artifact Recovery Report",
        "",
        f"status: {result['status']}",
        f"blocked_reason: {result['blocked_reason']}",
        f"baseline_recovery_status: {result['baseline_recovery_status']}",
        f"baseline_recovery_limited: {str(result['baseline_recovery_limited']).lower()}",
        "external_test_used_for_training: false",
        "external_test_used_for_threshold_tuning: false",
        "",
        "## Horizon Artifacts",
        "",
        "| horizon | status | feature_set | model | train samples | holdout samples | artifact | reason |",
        "|---|---|---|---|---:|---:|---|---|",
    ]
    for item in result["records"]:
        lines.append(
            f"| {item.get('horizon')}d | {item.get('status')} | {item.get('feature_set')} | "
            f"{item.get('model', '')} | {item.get('train_samples', 0)} | {item.get('holdout_samples', 0)} | "
            f"{item.get('artifact_path', '')} | {item.get('blocked_reason') or item.get('reason') or item.get('feature_selection_reason', '')} |"
        )
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- This recovery stage never reads Guinea 2021 external test records for training, model selection, or threshold tuning.",
            "- Rebuilt artifacts are traditional ML baselines only; no Qwen, LoRA, tmux, API, Gradio, or STEP continuous work is started.",
            "",
            "## JSON",
            "```json",
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True),
            "```",
        ]
    )
    report_dir = ensure_dir(resolve_path("data/outputs/reports"))
    write_json(report_dir / f"{REPORT_NAME}.json", result)
    (report_dir / f"{REPORT_NAME}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Recover or rebuild P0a traditional ML baseline artifacts for P0f.")
    parser.add_argument("--config", default="configs/config.yaml")
    args = parser.parse_args()
    result = recover_baseline_artifacts(args.config)
    print(json.dumps({"status": result["status"], "blocked_reason": result["blocked_reason"]}, ensure_ascii=False))
    return 0 if result["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
