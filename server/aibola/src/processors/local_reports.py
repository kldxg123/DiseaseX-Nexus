from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean

from src.models.qwen25_lora_dataset import validate_sft_records
from src.utils.config import load_config, resolve_path
from src.utils.io import ensure_dir, read_jsonl, read_table, write_json


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _date_range(records: list[dict]) -> tuple[str, str]:
    dates = sorted(str(row.get("date", ""))[:10] for row in records if row.get("date"))
    return (dates[0], dates[-1]) if dates else ("missing", "missing")


def _countries(records: list[dict]) -> list[str]:
    return sorted({str(row.get("country", "")).strip() for row in records if row.get("country")})


def _flag_counts(records: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for row in records:
        for flag in row.get("data_quality_flags", []) or []:
            counts[str(flag)] += 1
        if float(row.get("new_cases", 0.0) or 0.0) < 0:
            counts["negative_new_cases_value"] += 1
    return dict(sorted(counts.items()))


def _label_summary(processed: Path) -> dict[str, dict]:
    out = {}
    for path in sorted(processed.glob("trend_dataset_*d.jsonl")):
        records = read_jsonl(path)
        counts = Counter(str(row.get("label", "")) for row in records)
        out[path.stem.replace("trend_dataset_", "")] = {"samples": len(records), "label_counts": dict(counts)}
    return out


def _numeric_stats(records: list[dict], field: str) -> dict:
    values = []
    for row in records:
        try:
            values.append(float(row.get(field, 0.0) or 0.0))
        except (TypeError, ValueError):
            pass
    if not values:
        return {"count": 0}
    return {"count": len(values), "min": min(values), "max": max(values), "mean": round(mean(values), 4), "nonzero": sum(1 for v in values if v != 0)}


def build_inventory(cfg: dict) -> dict:
    raw = resolve_path(cfg["paths"]["data_raw"])
    processed = resolve_path(cfg["paths"]["data_processed"])
    outputs = resolve_path(cfg["paths"]["outputs"])
    repo = cfg["sources"]["cases"].get("github_repo", "houzengxiang-14588/ebola-prediction")
    manifest = _load_json(raw / "github" / repo.replace("/", "__") / "github_data_manifest.json")
    cases = read_table(processed / "ebola_cases_daily.parquet")
    panel = read_table(processed / "ebola_quant_panel.parquet")
    weather_daily = read_table(processed / "weather_daily.parquet")
    weather_features = read_table(processed / "weather_features.parquet")
    mobility = read_table(processed / "mobility_features.parquet")
    sft_train = read_jsonl(processed / "qwen25_sft_train.jsonl")
    sft_val = read_jsonl(processed / "qwen25_sft_val.jsonl")
    sft_test = read_jsonl(processed / "qwen25_sft_test.jsonl")
    sft_all = sft_train + sft_val + sft_test
    date_min, date_max = _date_range(cases)
    return {
        "github_manifest": manifest,
        "case_records": len(cases),
        "case_date_min": date_min,
        "case_date_max": date_max,
        "countries": _countries(cases),
        "case_numeric_stats": {
            "new_cases": _numeric_stats(cases, "new_cases"),
            "cum_cases": _numeric_stats(cases, "cum_cases"),
            "deaths": _numeric_stats(cases, "deaths"),
        },
        "case_quality_flags": _flag_counts(cases),
        "weather_daily_records": len(weather_daily),
        "weather_feature_records": len(weather_features),
        "mobility_feature_records": len(mobility),
        "panel_records": len(panel),
        "panel_columns": sorted(panel[0].keys()) if panel else [],
        "label_summary": _label_summary(processed),
        "sft_records": {"train": len(sft_train), "val": len(sft_val), "test": len(sft_test), "total": len(sft_all)},
        "sft_validation": validate_sft_records(sft_all) if sft_all else {"valid": False, "records": 0},
        "available_reports": sorted(path.name for path in (outputs / "reports").glob("*") if path.is_file()),
    }


def parse_gpu_probe(log_path: Path) -> dict:
    if not log_path.exists():
        return {"status": "unavailable", "reason": "probe_log_missing"}
    text = log_path.read_text(encoding="utf-8", errors="replace")
    if "NoValidConnectionsError" in text or "remote command failed" in text:
        return {"status": "unavailable", "reason": "ssh_probe_failed", "log_path": str(log_path)}
    memory_matches = re.findall(r"(\d+)MiB\s*/\s*(\d+)MiB", text)
    util_matches = re.findall(r"\|\s+(\d+)%\s+Default\s+\|", text)
    gpu_count = len(memory_matches)
    gpus = []
    for idx, (used, total) in enumerate(memory_matches):
        used_mib = int(used)
        total_mib = int(total)
        util = int(util_matches[idx]) if idx < len(util_matches) else None
        gpus.append(
            {
                "index": idx,
                "used_gb": round(used_mib / 1024, 2),
                "total_gb": round(total_mib / 1024, 2),
                "free_gb": round((total_mib - used_mib) / 1024, 2),
                "util_percent": util,
            }
        )
    disk_match = re.search(r"/dev/\S+\s+\S+\s+\S+\s+(\S+)\s+\S+\s+/home", text)
    memory_match = re.search(r"Mem:\s+(\S+)\s+(\S+)\s+(\S+)\s+\S+\s+\S+\s+(\S+)", text)
    return {
        "status": "ok" if gpus else "unavailable",
        "gpu_count": gpu_count,
        "gpus": gpus,
        "disk_available_home": disk_match.group(1) if disk_match else "unknown",
        "memory": {
            "total": memory_match.group(1),
            "used": memory_match.group(2),
            "free": memory_match.group(3),
            "available": memory_match.group(4),
        }
        if memory_match
        else {},
        "log_path": str(log_path),
    }


def write_reports(inventory: dict, gpu: dict, cfg: dict) -> None:
    reports = ensure_dir(resolve_path(cfg["paths"]["outputs"]) / "reports")
    write_json(reports / "local_data_inventory.json", inventory)
    write_json(reports / "gpu_resource_report.json", gpu)
    countries = inventory["countries"]
    label_summary = inventory["label_summary"]
    source_files = inventory.get("github_manifest", {}).get("downloaded_files", [])

    inventory_md = [
        "# Local Data Inventory",
        "",
        "## Collected Files",
        f"- GitHub status: {inventory.get('github_manifest', {}).get('status', 'missing')}",
        f"- Downloaded source files: {len(source_files)}",
        f"- Processed case records: {inventory['case_records']}",
        f"- Weather daily records: {inventory['weather_daily_records']}",
        f"- Weather feature records: {inventory['weather_feature_records']}",
        f"- Mobility proxy records: {inventory['mobility_feature_records']}",
        f"- Quant panel records: {inventory['panel_records']}",
        f"- Qwen SFT records: {json.dumps(inventory['sft_records'], ensure_ascii=False)}",
        "",
        "## Coverage",
        f"- Date range: {inventory['case_date_min']} to {inventory['case_date_max']}",
        f"- Countries: {len(countries)} ({', '.join(countries)})",
        "",
        "## Label Distribution",
    ]
    for horizon, item in label_summary.items():
        inventory_md.append(f"- {horizon}: {json.dumps(item, ensure_ascii=False)}")
    (reports / "local_data_inventory.md").write_text("\n".join(inventory_md) + "\n", encoding="utf-8")

    quality_md = [
        "# Data Quality Report",
        "",
        f"- Case records: {inventory['case_records']}",
        f"- Case date range: {inventory['case_date_min']} to {inventory['case_date_max']}",
        f"- Countries: {len(countries)}",
        f"- Case stats: {json.dumps(inventory['case_numeric_stats'], ensure_ascii=False)}",
        f"- Quality flags: {json.dumps(inventory['case_quality_flags'], ensure_ascii=False)}",
        "",
        "## Main Defects",
        "- `ebola_africa.csv` is annual and must not train 7/14/21/30-day trend labels.",
        "- `ebola_data_db_format.csv` is country-level daily data, not district-level transmission-chain data.",
        "- Negative `new_cases` values come from cumulative-report revisions and are retained as quality flags.",
        "- Coordinates are country-centroid proxies for many rows, not precise local outbreak coordinates.",
        "- Mobility fields are proxy estimates, not observed real daily passenger or road traffic volume.",
        "- Weather/ecology features are environmental correlates and must not be interpreted as direct causal proof.",
    ]
    (reports / "data_quality_report.md").write_text("\n".join(quality_md) + "\n", encoding="utf-8")

    preprocessing_md = [
        "# Preprocessing Summary",
        "",
        "- Main task: three-class trend classification, not exact case-count regression.",
        "- Main windows: 7d short-term and 21d medium-term; 14d and 30d are sensitivity windows.",
        "- Case cleaning: long WHO-like table is pivoted to cumulative/new cases, deaths, and quality flags.",
        "- Weather: Open-Meteo daily variables are joined with hourly variables aggregated locally to daily mean/max/min.",
        "- Mobility: P0 uses gravity-flow and infectious-inflow proxy math from local region metadata.",
        "- Time leakage guard: rolling features only use rows up to the prediction date; future windows only create labels.",
        f"- SFT validation: {json.dumps(inventory['sft_validation'], ensure_ascii=False)}",
    ]
    (reports / "preprocessing_summary.md").write_text("\n".join(preprocessing_md) + "\n", encoding="utf-8")

    blockers_md = [
        "# Project Blockers",
        "",
        "## Data",
        "- Real daily country-level Ebola data is available, but total outbreak diversity remains limited.",
        "- Upward-trend labels can be sparse relative to stable labels, so macro-F1 and upward recall matter more than accuracy alone.",
        "- No real daily global traffic/passenger-flow data is available in P0; current mobility variables are proxies.",
        "- Country-centroid weather joins may blur local ecological conditions.",
        "",
        "## Compute",
        "- Data download, cleaning, feature building, label building, and SFT formatting do not need GPU.",
        "- GPU is only needed for Qwen2.5-7B QLoRA training or large-model inference.",
        "- If remote GPU probe fails, training readiness cannot be confirmed from local reports.",
    ]
    (reports / "project_blockers.md").write_text("\n".join(blockers_md) + "\n", encoding="utf-8")

    gpu_md = [
        "# GPU Resource Report",
        "",
        f"- Probe status: {gpu.get('status')}",
        f"- GPU count: {gpu.get('gpu_count', 0)}",
        f"- GPUs: {json.dumps(gpu.get('gpus', []), ensure_ascii=False)}",
        f"- /home available disk: {gpu.get('disk_available_home', 'unknown')}",
        f"- Memory: {json.dumps(gpu.get('memory', {}), ensure_ascii=False)}",
        "",
        "## Expected Qwen2.5-7B Resource Use",
        "- 4-bit inference usually needs roughly single-digit to low-teens GB VRAM for low concurrency.",
        "- 4-bit QLoRA training should reserve roughly 16-30GB VRAM; with max_seq_length=4096, 30GB+ free VRAM is safer.",
        "- Training memory is higher than model-weight memory because it also stores activations, LoRA gradients, optimizer states, bitsandbytes buffers, and PyTorch/CUDA temporary memory.",
        "",
        "## Lower-Memory Options And Cost",
        "- Reduce max_seq_length 4096 -> 2048/1024: lower VRAM, but less room for RAG evidence and history.",
        "- Reduce LoRA r 16 -> 8: lower adapter/optimizer memory, but weaker adaptation capacity.",
        "- Keep gradient checkpointing enabled: lower VRAM, but slower training.",
        "- Switch to Qwen2.5-3B/1.5B: easier on RTX 4060-class GPUs, but trend reasoning and robustness may decline.",
    ]
    (reports / "gpu_resource_report.md").write_text("\n".join(gpu_md) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate local data and GPU resource reports.")
    parser.add_argument("--config", default="configs/config.yaml")
    parser.add_argument("--probe-log", default="logs/remote/probe_sanitized.log")
    args = parser.parse_args()
    cfg = load_config(args.config)
    inventory = build_inventory(cfg)
    gpu = parse_gpu_probe(resolve_path(args.probe_log))
    write_reports(inventory, gpu, cfg)
    print(json.dumps({"status": "ok", "case_records": inventory["case_records"], "gpu_status": gpu.get("status")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
