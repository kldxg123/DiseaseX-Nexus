from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from src.eval.metrics import LABEL_ORDER, LABEL_UP, classification_metrics
from src.models.qwen25_lora_dataset import parse_horizon, record_label
from src.utils.config import resolve_path
from src.utils.io import read_jsonl


ADAPTER_ID = "qwen25_lora_20260623_011633"
QWEN_SAMPLE_PATH = "data/outputs/reports/qwen_lora_eval_samples.jsonl"
FEATURE_SET = "cases_weather_mobility_proxy"


def load_json(path: str | Path) -> dict[str, Any]:
    p = resolve_path(path)
    if not p.exists():
        return {}
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def user_payload(record: dict[str, Any]) -> dict[str, Any]:
    messages = record.get("messages") or []
    text = " ".join(str(item.get("content", "")) for item in messages if isinstance(item, dict) and item.get("role") == "user")
    start = text.find("{")
    if start < 0:
        return {}
    try:
        payload = json.loads(text[start:])
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def sft_records(split: str) -> list[dict[str, Any]]:
    rows = read_jsonl(resolve_path(f"data/processed/qwen25_sft_{split}.jsonl"))
    output: list[dict[str, Any]] = []
    for idx, record in enumerate(rows):
        payload = user_payload(record)
        horizon = parse_horizon(record)
        label = record_label(record)
        output.append(
            {
                "split": split,
                "split_index": idx,
                "sample_id": f"{ADAPTER_ID}_{split}_{idx:06d}",
                "country": str(payload.get("country", "")),
                "adm1": str(payload.get("adm1", "")),
                "date": str(payload.get("date", ""))[:10],
                "horizon_days": horizon,
                "label": label,
                "features": payload.get("features") or {},
                "record": record,
            }
        )
    return output


def trend_dataset_lookup() -> dict[tuple[str, str, str, int], dict[str, Any]]:
    lookup: dict[tuple[str, str, str, int], dict[str, Any]] = {}
    for horizon in [7, 14, 21, 30]:
        for row in read_jsonl(resolve_path(f"data/processed/trend_dataset_{horizon}d.jsonl")):
            key = (
                str(row.get("country", "")),
                str(row.get("adm1", "")),
                str(row.get("date", ""))[:10],
                int(row.get("horizon_days") or horizon),
            )
            lookup[key] = row
    return lookup


def map_sft_to_trend() -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    lookup = trend_dataset_lookup()
    mapped: dict[str, list[dict[str, Any]]] = {}
    coverage: dict[str, Any] = {}
    for split in ["train", "val", "test"]:
        rows = sft_records(split)
        out: list[dict[str, Any]] = []
        reasons: Counter[str] = Counter()
        for item in rows:
            horizon = item.get("horizon_days")
            if horizon not in {7, 14, 21, 30}:
                reasons["horizon_not_parseable"] += 1
                continue
            key = (item["country"], item["adm1"], item["date"], int(horizon))
            source = lookup.get(key)
            if not source:
                reasons["trend_dataset_key_missing"] += 1
                continue
            merged = dict(source)
            merged["split"] = split
            merged["split_index"] = item["split_index"]
            merged["sft_sample_id"] = item["sample_id"]
            merged["label"] = item["label"] or source.get("label")
            out.append(merged)
        mapped[split] = out
        total = len(rows)
        coverage[split] = {
            "total": total,
            "mapped": len(out),
            "coverage": len(out) / total if total else 0.0,
            "unmapped_count": total - len(out),
            "unmapped_reason_summary": dict(reasons),
        }
    return mapped, coverage


def cross_horizon_leakage(mapped: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    groups: dict[tuple[str, str, str], set[tuple[str, int]]] = {}
    for split, rows in mapped.items():
        for row in rows:
            key = (str(row.get("country", "")), str(row.get("adm1", "")), str(row.get("date", ""))[:10])
            groups.setdefault(key, set()).add((split, int(row.get("horizon_days") or 0)))
    overlaps = []
    for key, values in groups.items():
        splits = {split for split, _ in values}
        horizons = {horizon for _, horizon in values}
        if len(splits) > 1 and len(horizons) > 1:
            overlaps.append({"country": key[0], "adm1": key[1], "date": key[2], "splits": sorted(splits), "horizons": sorted(horizons)})
    return {
        "cross_horizon_country_date_overlap_count": len(overlaps),
        "examples": overlaps[:20],
        "leakage_check_passed": len(overlaps) == 0,
    }


def qwen_samples(required: bool = True) -> list[dict[str, Any]]:
    path = resolve_path(QWEN_SAMPLE_PATH)
    rows = read_jsonl(path)
    if required and not rows:
        raise FileNotFoundError("missing_artifacts:qwen_lora_eval_samples.jsonl")
    return rows


def metric_bundle(y_true: list[str], y_pred: list[str], baseline: dict[str, Any] | None = None) -> dict[str, Any]:
    metrics = classification_metrics(y_true, y_pred, LABEL_ORDER)
    up = metrics["per_class"][LABEL_UP]
    false_positive_count = sum(1 for truth, pred in zip(y_true, y_pred) if pred == LABEL_UP and truth != LABEL_UP)
    non_up = sum(1 for truth in y_true if truth != LABEL_UP)
    stable_to_up_count = sum(1 for truth, pred in zip(y_true, y_pred) if truth == LABEL_ORDER[2] and pred == LABEL_UP)
    metrics.update(
        {
            "up_recall": up["recall"],
            "up_precision": up["precision"],
            "false_positive_count": false_positive_count,
            "false_alarm_rate": false_positive_count / non_up if non_up else 0.0,
            "stable_to_up_count": stable_to_up_count,
        }
    )
    if baseline:
        metrics["macro_f1_change_vs_qwen"] = metrics["macro_f1"] - float(baseline.get("macro_f1", 0.0))
        metrics["balanced_accuracy_change_vs_qwen"] = metrics["balanced_accuracy"] - float(baseline.get("balanced_accuracy", 0.0))
    return metrics


def sample_id_sort_key(sample: dict[str, Any]) -> str:
    return str(sample.get("sample_id") or sample.get("experience_id") or "")
