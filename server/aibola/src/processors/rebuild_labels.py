from __future__ import annotations

import argparse
import json

from src.models.qwen25_lora_dataset import chronological_split, make_qwen_sft_records, validate_sft_records
from src.processors.label_builder import build_labels
from src.utils.config import load_config, resolve_path
from src.utils.io import read_table, write_json, write_jsonl


def rebuild_labels(config_path: str = "configs/config.yaml") -> dict:
    cfg = load_config(config_path)
    processed = resolve_path(cfg["paths"]["data_processed"])
    reports = resolve_path(cfg["paths"]["outputs"]) / "reports"
    panel = read_table(processed / "ebola_quant_panel.parquet")
    all_samples = []
    label_summary = {}
    for horizon in cfg["data"]["horizons"]:
        samples = build_labels(
            panel,
            horizon_days=int(horizon),
            past_window_days=int(cfg["data"]["past_window_days"]),
            up_threshold=float(cfg["data"]["up_threshold"]),
            down_threshold=float(cfg["data"]["down_threshold"]),
        )
        write_jsonl(processed / f"trend_dataset_{horizon}d.jsonl", samples)
        all_samples.extend(samples)
        counts: dict[str, int] = {}
        for sample in samples:
            counts[sample["label"]] = counts.get(sample["label"], 0) + 1
        label_summary[f"{horizon}d"] = {"samples": len(samples), "label_counts": counts}
    train, val, test = chronological_split(sorted(all_samples, key=lambda r: r["date"]))
    train_records = make_qwen_sft_records(train)
    val_records = make_qwen_sft_records(val)
    test_records = make_qwen_sft_records(test)
    write_jsonl(processed / "qwen25_sft_train.jsonl", train_records)
    write_jsonl(processed / "qwen25_sft_val.jsonl", val_records)
    write_jsonl(processed / "qwen25_sft_test.jsonl", test_records)
    validation = validate_sft_records(train_records + val_records + test_records)
    summary = {"panel_records": len(panel), "label_summary": label_summary, "qwen_sft_validation": validation}
    write_json(reports / "rebuild_labels_summary.json", summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild trend labels and Qwen SFT files from the current panel.")
    parser.add_argument("--config", default="configs/config.yaml")
    args = parser.parse_args()
    summary = rebuild_labels(args.config)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["qwen_sft_validation"]["valid"] and summary["qwen_sft_validation"]["records"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
