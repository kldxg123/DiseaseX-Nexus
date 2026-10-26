from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from src.eval.external_validation_common import (
    HORIZONS,
    REPORTS_DIR,
    metric_bundle,
    write_confusion_matrix_figure,
    write_report_pair,
)
from src.models.qwen25_json_eval import ground_truth, parse_prediction, prompt_messages
from src.utils.config import resolve_path
from src.utils.io import read_jsonl, write_jsonl


ADAPTERS = {
    "v1": {
        "adapter_id": "qwen25_lora_20260623_011633",
        "adapter_dir": "lora_weights/ebola_qwen25_step/candidates/qwen25_lora_20260623_011633",
        "report_prefix": "external_qwen_v1_eval",
        "figure": "data/outputs/figures/external_qwen_v1_confusion_matrix.png",
        "predictions": "data/outputs/reports/external_qwen_v1_predictions.jsonl",
    },
    "v2": {
        "adapter_id": "qwen25_lora_v2_20260624_010911",
        "adapter_dir": "lora_weights/ebola_qwen25_step/candidates/qwen25_lora_v2_20260624_010911",
        "report_prefix": "external_qwen_v2_eval",
        "figure": "data/outputs/figures/external_qwen_v2_confusion_matrix.png",
        "predictions": "data/outputs/reports/external_qwen_v2_predictions.jsonl",
    },
}
REQUIRED_ADAPTER_FILES = ["adapter_config.json", "adapter_model.safetensors", "run_manifest.json", "train_metrics.json"]


def _set_offline_env() -> None:
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("HF_DATASETS_OFFLINE", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")


def _load_records() -> list[dict[str, Any]]:
    return read_jsonl(resolve_path("data/processed/external_validation/external_qwen_sft_test_all.jsonl"))


def _generate_outputs(model: Any, tokenizer: Any, records: list[dict[str, Any]]) -> list[str]:
    import torch

    outputs: list[str] = []
    device = next(model.parameters()).device
    for record in records:
        messages = prompt_messages(record)
        rendered = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        if not isinstance(rendered, str):
            rendered = str(rendered)
        input_ids = tokenizer(
            rendered,
            add_special_tokens=False,
            return_tensors="pt",
            truncation=True,
            max_length=4096,
        )["input_ids"].to(device)
        with torch.no_grad():
            generated = model.generate(
                input_ids=input_ids,
                do_sample=False,
                temperature=0,
                max_new_tokens=512,
                pad_token_id=tokenizer.eos_token_id,
            )
        outputs.append(tokenizer.decode(generated[0, input_ids.shape[-1] :], skip_special_tokens=True))
    return outputs


def _rows_and_metrics(records: list[dict[str, Any]], outputs: list[str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    y_true: list[str] = []
    y_pred: list[str] = []
    json_checks: list[dict[str, Any]] = []
    for record, raw in zip(records, outputs):
        truth = ground_truth(record)
        pred, checks = parse_prediction(raw)
        if truth.get("trend") is None:
            continue
        row = {
            "sample_id": record.get("sample_id"),
            "date": record.get("date"),
            "country": record.get("country"),
            "adm1": record.get("adm1"),
            "adm2": record.get("adm2"),
            "horizon_days": record.get("horizon_days") or truth.get("horizon_days"),
            "source_dataset": record.get("source_dataset"),
            "data_quality_flags": record.get("data_quality_flags", []),
            "true_label": truth.get("trend"),
            "predicted_label": pred.get("trend"),
            "confidence": pred.get("confidence"),
            "risk_score": pred.get("risk_score"),
            "evidence": pred.get("evidence"),
            "valid_json": checks.get("valid_json"),
            "repaired_json": checks.get("repaired_json"),
            "fallback_json": checks.get("fallback_json"),
            "evidence_list_valid": checks.get("evidence_list_valid"),
            "confidence_valid": checks.get("confidence_valid"),
            "risk_score_valid": checks.get("risk_score_valid"),
        }
        rows.append(row)
        y_true.append(str(row["true_label"]))
        y_pred.append(str(row["predicted_label"]))
        json_checks.append(checks)
    metrics = metric_bundle(y_true, y_pred)
    total = max(len(json_checks), 1)
    metrics_block = {
        "records": len(records),
        "evaluated_records": len(rows),
        "json_validity_rate": sum(1 for item in json_checks if item.get("valid_json")) / total,
        "invalid_json_count": sum(1 for item in json_checks if not item.get("valid_json")),
        "repaired_json_count": sum(1 for item in json_checks if item.get("repaired_json")),
        "fallback_json_count": sum(1 for item in json_checks if item.get("fallback_json")),
        "evidence_validity_rate": sum(1 for item in json_checks if item.get("evidence_list_valid")) / total,
        "confidence_range_validity_rate": sum(1 for item in json_checks if item.get("confidence_valid")) / total,
        "risk_score_range_validity_rate": sum(1 for item in json_checks if item.get("risk_score_valid")) / total,
        "metrics": metrics,
        "horizon_metrics": {},
    }
    for horizon in HORIZONS:
        subset = [row for row in rows if int(row.get("horizon_days") or 0) == horizon]
        key = f"{horizon}d"
        if horizon == 30 and not subset:
            metrics_block["horizon_metrics"][key] = {"status": "not_available", "reason": "no_external_test_samples", "sample_count": 0}
            continue
        yy = [str(row["true_label"]) for row in subset]
        pp = [str(row["predicted_label"]) for row in subset]
        metrics_block["horizon_metrics"][key] = {
            "status": "ok",
            "sample_count": len(subset),
            "low_n_warning": "low_n_warning" if horizon == 21 and subset else None,
            "metrics": metric_bundle(yy, pp),
        }
    return rows, metrics_block


def evaluate_external_qwen(version: str, model_dir: str = "models/Qwen2.5-7B-Instruct") -> dict[str, Any]:
    _set_offline_env()
    spec = ADAPTERS[version]
    records = _load_records()
    if not records:
        result = {
            "status": "blocked",
            "blocked_reason": "external_qwen_sft_test_all_missing_or_empty",
            "adapter_id": spec["adapter_id"],
            "promote_allowed": False,
        }
        write_external_qwen_report(version, result)
        return result
    adapter = resolve_path(spec["adapter_dir"])
    missing = [name for name in REQUIRED_ADAPTER_FILES if not (adapter / name).exists()]
    if missing:
        result = {
            "status": "blocked",
            "blocked_reason": "missing_adapter_artifacts:" + ",".join(missing),
            "adapter_id": spec["adapter_id"],
            "adapter_dir": spec["adapter_dir"],
            "promote_allowed": False,
        }
        write_external_qwen_report(version, result)
        return result
    try:
        import torch
        from peft import PeftModel
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    except Exception as exc:
        result = {
            "status": "blocked",
            "blocked_reason": "external_qwen_eval_import_failed",
            "error_type": type(exc).__name__,
            "adapter_id": spec["adapter_id"],
            "promote_allowed": False,
        }
        write_external_qwen_report(version, result)
        return result
    try:
        base_model = resolve_path(model_dir)
        tokenizer = AutoTokenizer.from_pretrained(base_model.as_posix(), local_files_only=True, trust_remote_code=False)
        if tokenizer.pad_token is None:
            tokenizer.pad_token = tokenizer.eos_token
        quantization_config = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_compute_dtype=torch.bfloat16)
        base = AutoModelForCausalLM.from_pretrained(
            base_model.as_posix(),
            local_files_only=True,
            trust_remote_code=False,
            quantization_config=quantization_config,
            device_map={"": 0},
        )
        model = PeftModel.from_pretrained(base, adapter.as_posix(), local_files_only=True)
        model.eval()
        outputs = _generate_outputs(model, tokenizer, records)
    except Exception as exc:
        result = {
            "status": "blocked",
            "blocked_reason": "external_qwen_inference_failed",
            "error_type": type(exc).__name__,
            "error": str(exc)[:500],
            "adapter_id": spec["adapter_id"],
            "promote_allowed": False,
        }
        write_external_qwen_report(version, result)
        return result
    rows, metrics_block = _rows_and_metrics(records, outputs)
    write_jsonl(resolve_path(spec["predictions"]), rows)
    write_confusion_matrix_figure(metrics_block["metrics"]["confusion_matrix"], spec["figure"], f"External Qwen {version} confusion matrix")
    result = {
        "status": "ok",
        "blocked_reason": "none",
        "adapter_id": spec["adapter_id"],
        "adapter_dir": spec["adapter_dir"],
        "metric_source": f"P0e external test; Qwen LoRA {version}; system+user prompt only",
        "ground_truth_handling": "assistant JSON parsed separately; never included in prompt",
        "generation_config": {"do_sample": False, "temperature": 0, "max_new_tokens": 512},
        "offline_env": {
            "TRANSFORMERS_OFFLINE": os.environ.get("TRANSFORMERS_OFFLINE"),
            "HF_HUB_OFFLINE": os.environ.get("HF_HUB_OFFLINE"),
            "HF_DATASETS_OFFLINE": os.environ.get("HF_DATASETS_OFFLINE"),
            "TOKENIZERS_PARALLELISM": os.environ.get("TOKENIZERS_PARALLELISM"),
        },
        **metrics_block,
        "predictions_path": spec["predictions"],
        "figure": spec["figure"],
        "promote_allowed": False,
        "allow_auto_promote": False,
    }
    write_external_qwen_report(version, result)
    return result


def write_external_qwen_report(version: str, result: dict[str, Any]) -> None:
    spec = ADAPTERS[version]
    lines = [
        f"# External Qwen {version.upper()} Eval",
        "",
        f"status: {result['status']}",
        f"blocked_reason: {result.get('blocked_reason', 'none')}",
        f"adapter_id: {result.get('adapter_id')}",
        "promote_allowed: false",
        "merge_performed: false",
        "training_started: false",
        "hf_access: false",
        "prompt_policy: system+user only; assistant JSON is ground truth only",
    ]
    if result.get("status") == "ok":
        metrics = result["metrics"]
        lines.extend(
            [
                f"json_validity_rate: {result['json_validity_rate']:.4f}",
                f"invalid_json_count: {result['invalid_json_count']}",
                f"repaired_json_count: {result['repaired_json_count']}",
                f"fallback_json_count: {result['fallback_json_count']}",
                f"trend_macro_f1: {metrics['macro_f1']:.4f}",
                f"balanced_accuracy: {metrics['balanced_accuracy']:.4f}",
                f"up_recall: {metrics['up_recall']:.4f}",
                f"up_precision: {metrics['up_precision']:.4f}",
                f"false_alarm_rate: {metrics['false_alarm_rate']:.4f}",
                f"stable_to_up_count: {metrics['stable_to_up_count']}",
                f"evidence_validity_rate: {result['evidence_validity_rate']:.4f}",
                f"confidence_range_validity_rate: {result['confidence_range_validity_rate']:.4f}",
                f"risk_score_range_validity_rate: {result['risk_score_range_validity_rate']:.4f}",
                "",
                "## By Horizon",
                "| horizon | samples | macro-F1 | balanced accuracy | up recall | up precision | false alarm rate | warning |",
                "|---|---:|---:|---:|---:|---:|---:|---|",
            ]
        )
        for horizon in [f"{h}d" for h in HORIZONS]:
            item = result["horizon_metrics"].get(horizon, {})
            if item.get("status") == "not_available":
                lines.append(f"| {horizon} | 0 |  |  |  |  |  | {item.get('reason')} |")
                continue
            hm = item.get("metrics", {})
            lines.append(
                f"| {horizon} | {item.get('sample_count', 0)} | {hm.get('macro_f1', 0):.4f} | "
                f"{hm.get('balanced_accuracy', 0):.4f} | {hm.get('up_recall', 0):.4f} | "
                f"{hm.get('up_precision', 0):.4f} | {hm.get('false_alarm_rate', 0):.4f} | {item.get('low_n_warning') or ''} |"
            )
    lines.extend(["", "## JSON", "```json", json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), "```"])
    write_report_pair(spec["report_prefix"], result, lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate Qwen LoRA adapter on external validation data.")
    parser.add_argument("--version", choices=sorted(ADAPTERS), required=True)
    parser.add_argument("--model-dir", default="models/Qwen2.5-7B-Instruct")
    args = parser.parse_args()
    result = evaluate_external_qwen(args.version, args.model_dir)
    print(json.dumps({"status": result["status"], "blocked_reason": result.get("blocked_reason", "none"), "version": args.version}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
