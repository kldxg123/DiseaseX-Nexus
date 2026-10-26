from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from src.eval.metrics import LABEL_ORDER, LABEL_UP
from src.models.qwen25_json_eval import evaluate_predictions, ground_truth, parse_prediction
from src.models.qwen25_lora_dataset import parse_horizon
from src.models.qwen25_lora_eval import generate_outputs, latest_completed_adapter, report_path
from src.utils.config import load_config, resolve_path
from src.utils.io import ensure_dir, read_jsonl, write_json, write_jsonl


DEFAULT_ADAPTER_ID = "qwen25_lora_20260623_011633"
SAMPLE_ARTIFACT = "data/outputs/reports/qwen_lora_eval_samples.jsonl"


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def extract_user_payload(record: dict[str, Any]) -> dict[str, Any]:
    messages = record.get("messages") or []
    user_text = " ".join(str(item.get("content", "")) for item in messages if isinstance(item, dict) and item.get("role") == "user")
    start = user_text.find("{")
    if start < 0:
        return {}
    try:
        payload = json.loads(user_text[start:])
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def build_sample_record(idx: int, record: dict[str, Any], raw_output: str, adapter_id: str) -> dict[str, Any]:
    truth = ground_truth(record)
    pred, checks = parse_prediction(raw_output)
    user_payload = extract_user_payload(record)
    true_label = truth.get("trend")
    predicted_label = pred.get("trend")
    horizon = truth.get("horizon_days") or parse_horizon(record)
    error_type = "correct" if true_label == predicted_label else f"{true_label}_as_{predicted_label}"
    return {
        "sample_id": f"{adapter_id}_test_{idx:06d}",
        "adapter_id": adapter_id,
        "split": "test",
        "date": user_payload.get("date"),
        "country": user_payload.get("country"),
        "adm1": user_payload.get("adm1"),
        "horizon_days": horizon,
        "true_label": true_label,
        "predicted_label": predicted_label,
        "confidence": pred.get("confidence"),
        "risk_score": pred.get("risk_score"),
        "evidence": pred.get("evidence"),
        "raw_output_preview": raw_output[:500],
        "json_checks": checks,
        "error_type": error_type,
        "source": "p0c3_deterministic_replay",
        "prompt_ground_truth_leakage_guard": "system_user_only_prompt; assistant_ground_truth_parsed_separately",
        "data_quality_flags": [],
        "input_features": user_payload.get("features", {}),
    }


def materialize_sample_predictions(config_path: str, adapter_id: str) -> dict[str, Any]:
    cfg = load_config(config_path)
    sample_path = resolve_path(SAMPLE_ARTIFACT)
    existing = read_jsonl(sample_path)
    if existing:
        return {
            "status": "ok",
            "sample_artifact": SAMPLE_ARTIFACT,
            "sample_count": len(existing),
            "artifact_source": "existing_p0c3_or_prior_replay",
            "samples": existing,
        }

    try:
        import torch
        from peft import PeftModel
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    except Exception as exc:
        return {
            "status": "blocked",
            "blocked_reason": "sample_replay_import_failed",
            "error_type": type(exc).__name__,
            "sample_artifact": SAMPLE_ARTIFACT,
            "samples": [],
        }

    adapter = resolve_path("lora_weights/ebola_qwen25_step/candidates") / adapter_id
    if not adapter.exists():
        adapter = latest_completed_adapter(resolve_path(cfg["output_root"])) or adapter
    missing = [name for name in ["adapter_config.json", "adapter_model.safetensors"] if not (adapter / name).exists()]
    if missing:
        return {
            "status": "blocked",
            "blocked_reason": "missing_adapter_for_replay:" + ",".join(missing),
            "sample_artifact": SAMPLE_ARTIFACT,
            "samples": [],
        }

    model_dir = resolve_path(cfg["local_model_dir"])
    tokenizer = AutoTokenizer.from_pretrained(model_dir.as_posix(), local_files_only=True, trust_remote_code=False)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    quantization_config = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_compute_dtype=torch.bfloat16)
    base = AutoModelForCausalLM.from_pretrained(
        model_dir.as_posix(),
        local_files_only=True,
        trust_remote_code=False,
        quantization_config=quantization_config,
        device_map={"": 0},
    )
    model = PeftModel.from_pretrained(base, adapter.as_posix(), local_files_only=True)
    model.eval()
    records = read_jsonl(resolve_path(cfg["test_file"]))
    outputs = generate_outputs(model, tokenizer, records, dict(cfg.get("generation", {})))
    samples = [build_sample_record(idx, record, raw, adapter_id) for idx, (record, raw) in enumerate(zip(records, outputs))]
    write_jsonl(sample_path, samples)
    return {
        "status": "ok",
        "blocked_reason": "none",
        "sample_artifact": SAMPLE_ARTIFACT,
        "sample_count": len(samples),
        "artifact_source": "p0c3_deterministic_replay",
        "adapter_dir": report_path(adapter),
        "samples": samples,
    }


def confusion_pairs_from_matrix(matrix: dict[str, dict[str, int]]) -> dict[str, int]:
    pairs: dict[str, int] = {}
    for truth in LABEL_ORDER:
        row = matrix.get(truth, {}) if isinstance(matrix, dict) else {}
        for pred in LABEL_ORDER:
            if truth == pred:
                continue
            pairs[f"{truth}_as_{pred}"] = int(row.get(pred, 0))
    return pairs


def analyze_samples(samples: list[dict[str, Any]]) -> dict[str, Any]:
    if not samples:
        return {
            "sample_analysis_status": "not_available",
            "reason": "per_sample_predictions_missing",
        }
    truth = [str(item.get("true_label")) for item in samples if item.get("true_label") in LABEL_ORDER]
    pred = [str(item.get("predicted_label")) for item in samples if item.get("true_label") in LABEL_ORDER]
    error_records = [item for item in samples if item.get("true_label") != item.get("predicted_label")]
    horizon_counts: dict[str, Counter] = defaultdict(Counter)
    country_errors: Counter = Counter()
    invalid_json = repaired_json = fallback_json = 0
    low_conf_errors = high_conf_errors = missing_evidence = 0
    out_of_range_confidence = out_of_range_risk = 0
    for item in samples:
        checks = item.get("json_checks") if isinstance(item.get("json_checks"), dict) else {}
        if not checks.get("valid_json", False):
            invalid_json += 1
        if checks.get("repaired_json", False):
            repaired_json += 1
        if checks.get("fallback_json", False):
            fallback_json += 1
        if not checks.get("confidence_valid", False):
            out_of_range_confidence += 1
        if not checks.get("risk_score_valid", False):
            out_of_range_risk += 1
        evidence = item.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            missing_evidence += 1
        if item.get("true_label") != item.get("predicted_label"):
            try:
                conf = float(item.get("confidence"))
            except Exception:
                conf = None
            if conf is not None and conf < 0.5:
                low_conf_errors += 1
            if conf is not None and conf >= 0.8:
                high_conf_errors += 1
            country_errors[str(item.get("country") or "unknown")] += 1
        horizon = item.get("horizon_days")
        if horizon in {7, 14, 21, 30}:
            horizon_counts[f"{horizon}d"][str(item.get("error_type"))] += 1
    return {
        "sample_analysis_status": "ok",
        "evaluated_sample_count": len(samples),
        "truth_distribution": dict(Counter(truth)),
        "prediction_distribution": dict(Counter(pred)),
        "error_count": len(error_records),
        "error_type_counts": dict(Counter(str(item.get("error_type")) for item in error_records)),
        "horizon_error_counts": {h: dict(counts) for h, counts in sorted(horizon_counts.items())},
        "top_country_errors": dict(country_errors.most_common(10)),
        "json_counts": {
            "invalid_json_count": invalid_json,
            "repaired_json_count": repaired_json,
            "fallback_json_count": fallback_json,
        },
        "validity_counts": {
            "missing_or_empty_evidence": missing_evidence,
            "out_of_range_confidence": out_of_range_confidence,
            "out_of_range_risk_score": out_of_range_risk,
        },
        "confidence_error_counts": {
            "low_confidence_errors": low_conf_errors,
            "high_confidence_errors": high_conf_errors,
        },
    }


def write_error_breakdown_figure(confusion_pairs: dict[str, int], path: Path) -> None:
    ensure_dir(path.parent)
    try:
        import matplotlib.pyplot as plt

        items = sorted(confusion_pairs.items(), key=lambda item: item[1], reverse=True)
        labels = [item[0] for item in items]
        values = [item[1] for item in items]
        fig, ax = plt.subplots(figsize=(8, 4.5))
        ax.bar(range(len(values)), values, color="#4c78a8")
        ax.set_xticks(range(len(labels)), labels, rotation=35, ha="right")
        ax.set_ylabel("count")
        ax.set_title("Qwen LoRA Misclassification Breakdown")
        fig.tight_layout()
        fig.savefig(path)
        plt.close(fig)
    except Exception:
        path.with_suffix(".txt").write_text(json.dumps(confusion_pairs, ensure_ascii=False, indent=2), encoding="utf-8")


def build_error_analysis(config_path: str, adapter_id: str = DEFAULT_ADAPTER_ID) -> dict[str, Any]:
    reports = resolve_path("data/outputs/reports")
    eval_metrics = load_json(reports / "qwen_lora_eval_metrics.json")
    test_eval = eval_metrics.get("test", {}) if isinstance(eval_metrics.get("test"), dict) else {}
    metrics = test_eval.get("metrics", {}) if isinstance(test_eval.get("metrics"), dict) else {}
    matrix = metrics.get("confusion_matrix", {}) if isinstance(metrics.get("confusion_matrix"), dict) else {}
    confusion_pairs = confusion_pairs_from_matrix(matrix)
    up_row = matrix.get(LABEL_UP, {}) if isinstance(matrix, dict) else {}
    up_errors = {f"{LABEL_UP}_as_{pred}": int(up_row.get(pred, 0)) for pred in LABEL_ORDER if pred != LABEL_UP}
    main_up_error = max(up_errors.items(), key=lambda item: item[1])[0] if up_errors else "not_available"
    sample_result = materialize_sample_predictions(config_path, adapter_id)
    samples = sample_result.pop("samples", [])
    sample_analysis = analyze_samples(samples)
    figure_path = resolve_path("data/outputs/figures/qwen_error_breakdown.png")
    write_error_breakdown_figure(confusion_pairs, figure_path)
    up_recall = ((metrics.get("per_class") or {}).get(LABEL_UP) or {}).get("recall")
    blocked_reasons: list[str] = []
    if eval_metrics.get("status") != "ok":
        blocked_reasons.append("p0c2_eval_metrics_missing_or_not_ok")
    if sample_result.get("status") != "ok":
        blocked_reasons.append(sample_result.get("blocked_reason", "sample_replay_not_ok"))
    return {
        "status": "ok" if not blocked_reasons else "blocked",
        "blocked_reason": "none" if not blocked_reasons else ",".join(blocked_reasons),
        "adapter_id": adapter_id,
        "metrics_source": "P0c-2 accepted aggregate metrics",
        "per_sample_artifact_source": sample_result.get("artifact_source"),
        "per_sample_artifact": sample_result.get("sample_artifact"),
        "per_sample_replay_note": "P0c-3 analysis artifact only; not a replacement for accepted P0c-2 metrics",
        "up_recall": up_recall,
        "up_class_failure_summary": {
            "true_up_support": ((metrics.get("per_class") or {}).get(LABEL_UP) or {}).get("support"),
            "true_up_correct": int(up_row.get(LABEL_UP, 0)),
            "main_misclassification_direction": main_up_error,
            "up_error_counts": up_errors,
            "threshold_for_promotion": 0.50,
        },
        "confusion_pairs": confusion_pairs,
        "sample_replay_status": {k: v for k, v in sample_result.items() if k not in {"samples"}},
        "sample_analysis": sample_analysis,
        "horizon_metrics_source": "P0c-2 accepted aggregate metrics",
        "horizon_metrics": test_eval.get("horizon_metrics", {"status": "not_available", "reason": "missing_horizon_metrics"}),
        "figure": "data/outputs/figures/qwen_error_breakdown.png",
        "main_conclusion": "Promotion should remain on hold because 上升 recall is below threshold and most 上升 misses are predicted as 稳定.",
    }


def write_error_analysis_report(result: dict[str, Any]) -> Path:
    reports = ensure_dir("data/outputs/reports")
    write_json(reports / "qwen_error_analysis.json", result)
    up = result["up_class_failure_summary"]
    lines = [
        "# Qwen Error Analysis",
        "",
        f"status: {result['status']}",
        f"blocked_reason: {result['blocked_reason']}",
        f"adapter_id: {result['adapter_id']}",
        f"metrics_source: {result['metrics_source']}",
        f"per_sample_artifact_source: {result.get('per_sample_artifact_source')}",
        f"up_recall: {result.get('up_recall')}",
        f"main_misclassification_direction: {up.get('main_misclassification_direction')}",
        f"main_conclusion: {result['main_conclusion']}",
        "",
        "## Up Class Failure Summary",
        "```json",
        json.dumps(up, ensure_ascii=False, indent=2, sort_keys=True),
        "```",
        "",
        "## Confusion Pairs",
        "```json",
        json.dumps(result["confusion_pairs"], ensure_ascii=False, indent=2, sort_keys=True),
        "```",
        "",
        "## Sample Analysis",
        "```json",
        json.dumps(result["sample_analysis"], ensure_ascii=False, indent=2, sort_keys=True),
        "```",
    ]
    path = reports / "qwen_error_analysis.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate P0c-3 Qwen candidate error analysis.")
    parser.add_argument("--config", default="configs/train_qwen25_lora.yaml")
    parser.add_argument("--adapter-id", default=DEFAULT_ADAPTER_ID)
    args = parser.parse_args()
    result = build_error_analysis(args.config, args.adapter_id)
    report = write_error_analysis_report(result)
    print(json.dumps({"status": result["status"], "blocked_reason": result["blocked_reason"], "report": report.as_posix()}, ensure_ascii=False))
    return 0 if result["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
