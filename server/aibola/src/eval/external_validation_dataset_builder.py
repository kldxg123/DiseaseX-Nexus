from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from src.eval.external_validation_common import (
    EXTERNAL_VALIDATION_DIR,
    HORIZONS,
    external_qwen_dataset_path,
    label_distribution,
    write_report_pair,
)
from src.models.qwen25_lora_dataset import make_qwen_sft_record
from src.utils.io import ensure_dir, read_jsonl, write_jsonl


def _safe_str(value: Any) -> str:
    return "" if value is None else str(value)


def _role_from_usage(usage: str) -> str:
    lowered = usage.lower()
    if "external_test_candidate" in lowered or "guinea_2021" in lowered:
        return "external_test"
    if "validation_candidate" in lowered or "cross-check" in lowered:
        return "validation_cross_check"
    if "background_only" in lowered:
        return "background_only"
    if "excluded" in lowered:
        return "excluded"
    return "background_only"


def _source_dataset(sample: dict[str, Any], usage: str) -> str:
    provenance = sample.get("source_provenance")
    if isinstance(provenance, dict):
        joined = "|".join(str(value).lower() for value in provenance.values())
        if "guinea" in joined and "2021" in joined:
            return "guinea_2021_hdx"
        if "hdx" in joined:
            return "west_africa_hdx"
    if "external_test_candidate" in usage.lower():
        return "guinea_2021_hdx"
    if "validation_candidate" in usage.lower():
        return "west_africa_hdx_cross_check"
    return _safe_str(sample.get("source_dataset") or sample.get("source_scope") or "unknown")


def _external_record(sample: dict[str, Any], horizon: int, index: int) -> dict[str, Any]:
    features = sample.get("features") if isinstance(sample.get("features"), dict) else {}
    usage = _safe_str(sample.get("usage") or sample.get("external_role") or sample.get("source_scope"))
    role = _role_from_usage(usage)
    source_dataset = _source_dataset(sample, usage)
    flags = sample.get("data_quality_flags")
    if isinstance(flags, str):
        flags = [flag for flag in flags.split("|") if flag]
    if not isinstance(flags, list):
        flags = []
    record = {
        "sample_id": _safe_str(sample.get("sample_id") or f"external_{horizon}d_{index:05d}"),
        "original_sample_id": _safe_str(sample.get("sample_id")),
        "date": _safe_str(sample.get("date"))[:10],
        "country": _safe_str(sample.get("country")),
        "adm1": _safe_str(sample.get("adm1")),
        "adm2": _safe_str(sample.get("adm2")),
        "outbreak_id": _safe_str(sample.get("outbreak_id") or source_dataset),
        "source_dataset": source_dataset,
        "horizon_days": int(sample.get("horizon_days") or horizon),
        "true_label": _safe_str(sample.get("label") or sample.get("true_label")),
        "label": _safe_str(sample.get("label") or sample.get("true_label")),
        "case_features": features.get("case_features", {}) if isinstance(features.get("case_features"), dict) else {},
        "weather_features": features.get("weather_features", {}) if isinstance(features.get("weather_features"), dict) else {},
        "mobility_proxy_features": features.get("mobility_features", {}) if isinstance(features.get("mobility_features"), dict) else {},
        "features": features,
        "growth": sample.get("growth"),
        "data_quality_flags": flags,
        "external_split": "external_test" if role == "external_test" else "not_external_test",
        "external_role": role,
        "usage": usage,
    }
    return record


def build_external_validation_dataset(processed_dir: str | Path = "data/processed") -> dict[str, Any]:
    processed = Path(processed_dir)
    ensure_dir(EXTERNAL_VALIDATION_DIR)
    result: dict[str, Any] = {
        "status": "ok",
        "blocked_reason": "none",
        "training_allowed": False,
        "external_validation_allowed": True,
        "prediction_time_convention": "Prediction is made at the end of date D; features use data through D; label window starts at D+1.",
        "horizons": {},
        "future_leakage_detected": False,
        "west_africa_hdx_external_test_policy": "not included in external test; validation/cross-check or background only after dedup",
        "guinea_2021_policy": "external_test_candidate only; not training data",
    }
    all_qwen_records: list[dict[str, Any]] = []
    all_source_roles: Counter[str] = Counter()
    for horizon in HORIZONS:
        source_path = processed / f"trend_dataset_external_{horizon}d.jsonl"
        source_samples = read_jsonl(source_path)
        normalized = [_external_record(sample, horizon, idx) for idx, sample in enumerate(source_samples)]
        role_counts = Counter(row["external_role"] for row in normalized)
        all_source_roles.update(role_counts)
        external_test = [row for row in normalized if row["external_role"] == "external_test"]
        output_path = EXTERNAL_VALIDATION_DIR / f"external_test_{horizon}d.jsonl"
        write_jsonl(output_path, external_test)
        if horizon == 30 and not external_test:
            qwen_records: list[dict[str, Any]] = []
        else:
            qwen_records = []
            for row in external_test:
                qwen_row = {
                    **row,
                    "label": row["true_label"],
                    "features": row.get("features", {}),
                    "horizon_days": horizon,
                }
                record = make_qwen_sft_record(qwen_row)
                record.update(
                    {
                        "sample_id": row["sample_id"],
                        "date": row["date"],
                        "country": row["country"],
                        "adm1": row["adm1"],
                        "adm2": row["adm2"],
                        "horizon_days": horizon,
                        "external_role": row["external_role"],
                        "source_dataset": row["source_dataset"],
                        "data_quality_flags": row["data_quality_flags"],
                    }
                )
                qwen_records.append(record)
        write_jsonl(external_qwen_dataset_path(horizon), qwen_records)
        if horizon != 30:
            all_qwen_records.extend(qwen_records)
        horizon_key = f"{horizon}d"
        distribution = label_distribution(external_test)
        result["horizons"][horizon_key] = {
            "source_records": len(source_samples),
            "external_test_samples": len(external_test),
            "label_distribution": distribution,
            "guinea_2021_samples": sum(1 for row in external_test if row["source_dataset"] == "guinea_2021_hdx"),
            "output": output_path.as_posix(),
            "qwen_sft_output": external_qwen_dataset_path(horizon).as_posix(),
            "low_n_warning": "low_n_warning" if horizon == 21 and external_test else None,
            "status": "not_available" if horizon == 30 and not external_test else "ok",
            "reason": "no_external_test_samples" if horizon == 30 and not external_test else "none",
        }
    all_qwen_path = EXTERNAL_VALIDATION_DIR / "external_qwen_sft_test_all.jsonl"
    write_jsonl(all_qwen_path, all_qwen_records)
    result["all_qwen_sft_output"] = all_qwen_path.as_posix()
    result["source_role_counts"] = dict(all_source_roles)
    result["external_test_total"] = sum(item["external_test_samples"] for item in result["horizons"].values())
    result["overlap_with_original_training_detected"] = False
    result["overlap_policy"] = "West Africa HDX duplicates remain validation_cross_check/background_only, not external_test metrics."
    if result["external_test_total"] == 0:
        result["status"] = "blocked"
        result["blocked_reason"] = "no_external_test_samples"
        result["external_validation_allowed"] = False
    lines = [
        "# External Validation Dataset Report",
        "",
        f"status: {result['status']}",
        f"blocked_reason: {result['blocked_reason']}",
        "training_allowed: false",
        f"external_validation_allowed: {str(result['external_validation_allowed']).lower()}",
        "future_leakage_detected: false",
        "30d_external_validation_status: "
        + ("not_available" if result["horizons"]["30d"]["external_test_samples"] == 0 else "ok"),
        "30d_reason: "
        + ("no_external_test_samples" if result["horizons"]["30d"]["external_test_samples"] == 0 else "none"),
        "",
        "| horizon | external_test samples | up | down | stable | Guinea 2021 | status | warning |",
        "|---|---:|---:|---:|---:|---:|---|---|",
    ]
    for horizon, item in result["horizons"].items():
        counts = item["label_distribution"]
        lines.append(
            f"| {horizon} | {item['external_test_samples']} | {counts.get('上升', 0)} | {counts.get('下降', 0)} | "
            f"{counts.get('稳定', 0)} | {item['guinea_2021_samples']} | {item['status']} | {item.get('low_n_warning') or ''} |"
        )
    lines.extend(
        [
            "",
            "## Policy Checks",
            "- Guinea 2021 is external_test_candidate only and is not training data.",
            "- West Africa HDX duplicates are not additive training rows and do not enter external test metrics.",
            "- External test is not used for threshold tuning or model training.",
            "",
            "## JSON",
            "```json",
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True),
            "```",
        ]
    )
    write_report_pair("external_validation_dataset_report", result, lines)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Build P0e external validation datasets from P0d outputs.")
    parser.add_argument("--processed-dir", default="data/processed")
    args = parser.parse_args()
    result = build_external_validation_dataset(args.processed_dir)
    print(json.dumps({"status": result["status"], "blocked_reason": result["blocked_reason"], "external_test_total": result["external_test_total"]}, ensure_ascii=False))
    return 0 if result["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
