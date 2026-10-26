from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.collectors.ebola_case_loader import load_case_files, write_data_quality_report
from src.collectors.github_case_downloader import download_github_data
from src.models.qwen25_lora_dataset import make_qwen_sft_records, chronological_split, validate_sft_records
from src.processors.feature_builder import build_case_features, join_features
from src.processors.label_builder import build_labels
from src.utils.config import load_config, resolve_path
from src.utils.io import ensure_dir, write_json, write_jsonl, write_table


def _real_case_paths(cfg: dict, github_manifest: dict | None = None) -> list[Path]:
    paths = [resolve_path(path) for path in cfg["sources"]["cases"].get("local_files", [])]
    if github_manifest:
        paths.extend(Path(path) for path in github_manifest.get("csv_files", []))
    return paths


def prepare_non_gpu_data(config_path: str = "configs/config.yaml", download_github: bool = True) -> dict:
    cfg = load_config(config_path)
    processed = ensure_dir(resolve_path(cfg["paths"]["data_processed"]))
    reports = ensure_dir(resolve_path(cfg["paths"]["outputs"]) / "reports")
    github_manifest = None
    if download_github:
        repo = cfg["sources"]["cases"].get("github_repo", "houzengxiang-14588/ebola-prediction")
        github_manifest = download_github_data(
            repo,
            out_dir=resolve_path(cfg["paths"]["data_raw"]) / "github" / repo.replace("/", "__"),
            subdir="data",
        )
    case_paths = _real_case_paths(cfg, github_manifest)
    records, source_reports = load_case_files(case_paths)
    write_table(processed / "ebola_cases_daily.parquet", records)
    weather_points = build_weather_points(records)
    write_jsonl(processed / "weather_points.jsonl", weather_points)
    quality_path = write_data_quality_report(reports / "data_quality_report.md", source_reports, records)
    case_features = build_case_features(records)
    region_metadata = build_region_metadata(case_features)
    write_table(processed / "region_metadata.parquet", region_metadata)
    panel = join_features(case_features, weather_features=[], mobility_features=[])
    write_table(processed / "ebola_quant_panel.parquet", panel)
    label_summary = {}
    sft_validation = {"valid": False, "records": 0, "errors": ["no_samples"]}
    all_samples = []
    for horizon in cfg["data"]["horizons"]:
        samples = build_labels(
            panel,
            horizon_days=int(horizon),
            past_window_days=int(cfg["data"]["past_window_days"]),
            up_threshold=float(cfg["data"]["up_threshold"]),
            down_threshold=float(cfg["data"]["down_threshold"]),
        )
        all_samples.extend(samples)
        write_jsonl(processed / f"trend_dataset_{horizon}d.jsonl", samples)
        counts = {}
        for sample in samples:
            counts[sample["label"]] = counts.get(sample["label"], 0) + 1
        label_summary[f"{horizon}d"] = {"samples": len(samples), "label_counts": counts}
    if all_samples:
        train, val, test = chronological_split(sorted(all_samples, key=lambda r: r["date"]))
        train_records = make_qwen_sft_records(train)
        val_records = make_qwen_sft_records(val)
        test_records = make_qwen_sft_records(test)
        write_jsonl(processed / "qwen25_sft_train.jsonl", train_records)
        write_jsonl(processed / "qwen25_sft_val.jsonl", val_records)
        write_jsonl(processed / "qwen25_sft_test.jsonl", test_records)
        sft_validation = validate_sft_records(train_records + val_records + test_records)
    summary = {
        "github_manifest": github_manifest,
        "case_records": len(records),
        "case_sources": source_reports,
        "weather_points": len(weather_points),
        "region_metadata_records": len(region_metadata),
        "panel_records": len(panel),
        "label_summary": label_summary,
        "qwen_sft_validation": sft_validation,
        "data_quality_report": str(quality_path),
    }
    write_json(reports / "prepare_data_summary.json", summary)
    md = [
        "# Non-GPU Data Preparation Summary",
        "",
        f"case_records: {len(records)}",
        f"weather_points: {len(weather_points)}",
        f"region_metadata_records: {len(region_metadata)}",
        f"panel_records: {len(panel)}",
        "",
    ]
    if github_manifest:
        md.append(f"github_status: {github_manifest.get('status')}")
        md.append(f"github_csv_files: {len(github_manifest.get('csv_files', []))}")
        if github_manifest.get("error_type"):
            md.append(f"github_error_type: {github_manifest.get('error_type')}")
    md.append("")
    md.append("## Labels")
    for horizon, item in label_summary.items():
        md.append(f"- {horizon}: {json.dumps(item, ensure_ascii=False)}")
    md.append("")
    md.append("Training remains blocked unless daily/weekly real data has enough samples and label coverage.")
    (reports / "prepare_data_summary.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    return summary


def build_weather_points(records: list[dict], max_points: int = 20) -> list[dict]:
    grouped: dict[tuple[str, str], dict] = {}
    for record in records:
        lat = float(record.get("lat", 0.0) or 0.0)
        lon = float(record.get("lon", 0.0) or 0.0)
        if not lat or not lon:
            continue
        key = (record.get("country", ""), record.get("adm1", ""))
        item = grouped.setdefault(
            key,
            {
                "country": record.get("country", ""),
                "adm1": record.get("adm1", ""),
                "lat": lat,
                "lon": lon,
                "start_date": record.get("date"),
                "end_date": record.get("date"),
            },
        )
        item["start_date"] = min(item["start_date"], record.get("date"))
        item["end_date"] = max(item["end_date"], record.get("date"))
    return sorted(grouped.values(), key=lambda r: (r["country"], r["adm1"]))[:max_points]


def build_region_metadata(case_features: list[dict]) -> list[dict]:
    latest_by_region: dict[tuple[str, str], dict] = {}
    for record in case_features:
        key = (str(record.get("country", "")), str(record.get("adm1", "")))
        current = latest_by_region.get(key)
        if current is None or str(record.get("date", "")) > str(current.get("date", "")):
            latest_by_region[key] = record
    metadata = []
    for record in sorted(latest_by_region.values(), key=lambda r: (str(r.get("country", "")), str(r.get("adm1", "")))):
        flags = list(record.get("data_quality_flags", []) or [])
        if "population_proxy_default_1m" not in flags:
            flags.append("population_proxy_default_1m")
        metadata.append(
            {
                "country": record.get("country", ""),
                "adm1": record.get("adm1", ""),
                "lat": record.get("lat", 0.0),
                "lon": record.get("lon", 0.0),
                "population": 1_000_000.0,
                "population_density": 1.0,
                "new_cases_14d_sum": record.get("new_cases_14d_sum", 0.0),
                "active_cases": record.get("new_cases_14d_sum", 0.0),
                "road_accessibility_index": 1.0,
                "source": "case_centroid_proxy",
                "data_quality_flags": flags,
            }
        )
    return metadata


def main() -> int:
    parser = argparse.ArgumentParser(description="Run non-GPU data download, cleaning, feature and label preparation.")
    parser.add_argument("--config", default="configs/config.yaml")
    parser.add_argument("--no-github", action="store_true")
    args = parser.parse_args()
    summary = prepare_non_gpu_data(args.config, download_github=not args.no_github)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["case_records"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
