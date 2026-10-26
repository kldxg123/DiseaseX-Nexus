from __future__ import annotations

import json
import math
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.api.server import predict_trend
from src.eval.backtest import walk_forward_backtest
from src.models.baseline_lightgbm import train_and_evaluate
from src.models.qwen25_lora_dataset import make_qwen_sft_records, validate_sft_records
from src.processors.feature_builder import build_case_features, join_features
from src.processors.label_builder import build_labels
from src.processors.mobility_proxy_builder import build_mobility_features
from src.processors.weather_aggregator import rolling_weather_features
from src.utils.io import ensure_dir, write_json, write_jsonl, write_table


COUNTRIES = [
    ("Sierra Leone", "Eastern Province", "SLE", 8.46, -11.78, 850000),
    ("Liberia", "Lofa", "LBR", 7.90, -9.95, 550000),
    ("Guinea", "Nzerekore", "GIN", 7.75, -8.82, 620000),
]


def synthetic_cases(days: int = 90) -> list[dict]:
    start = date(2014, 7, 1)
    records = []
    for cidx, (country, adm1, iso3, lat, lon, population) in enumerate(COUNTRIES):
        cumulative = 0.0
        for offset in range(days):
            d = start + timedelta(days=offset)
            if cidx == 0:
                new = max(0, 2 + offset // 8 + (offset % 5))
            elif cidx == 1:
                new = max(0, 16 - offset // 7 + (offset % 3))
            else:
                new = max(0, 5 + int(2 * math.sin(offset / 5)))
            cumulative += new
            records.append(
                {
                    "date": d.isoformat(),
                    "country": country,
                    "iso3": iso3,
                    "adm1": adm1,
                    "adm2": "",
                    "lat": lat,
                    "lon": lon,
                    "confirmed": cumulative,
                    "probable": 0,
                    "suspected": 0,
                    "deaths": cumulative * 0.35,
                    "new_cases": new,
                    "cum_cases": cumulative,
                    "source": "synthetic_smoke_test",
                    "population": population,
                    "data_quality_flags": ["synthetic_smoke_test"],
                }
            )
    return records


def synthetic_weather(days: int = 90) -> list[dict]:
    start = date(2014, 7, 1)
    records = []
    for cidx, (country, adm1, _iso3, lat, lon, _population) in enumerate(COUNTRIES):
        for offset in range(days):
            d = start + timedelta(days=offset)
            records.append(
                {
                    "date": d.isoformat(),
                    "country": country,
                    "adm1": adm1,
                    "lat": lat,
                    "lon": lon,
                    "temperature_2m_mean": 24 + cidx + math.sin(offset / 10),
                    "relative_humidity_2m_mean": 70 + cidx * 3,
                    "precipitation_sum": max(0, 8 * math.sin(offset / 6)),
                    "source": "synthetic_smoke_test",
                }
            )
    return rolling_weather_features(records)


def synthetic_mobility() -> list[dict]:
    rows = []
    for country, adm1, _iso3, lat, lon, population in COUNTRIES:
        rows.append(
            {
                "country": country,
                "adm1": adm1,
                "lat": lat,
                "lon": lon,
                "population": population,
                "active_cases": 10,
                "road_accessibility_index": 0.7,
            }
        )
    return build_mobility_features(rows)


def run_smoke() -> dict:
    processed = ensure_dir("data/processed")
    reports = ensure_dir("data/outputs/reports")
    cases = synthetic_cases()
    weather = synthetic_weather()
    mobility = synthetic_mobility()
    panel = join_features(build_case_features(cases), weather, mobility)
    all_samples = []
    for horizon in (7, 14, 30):
        samples = build_labels(panel, horizon_days=horizon)
        write_jsonl(processed / f"trend_dataset_{horizon}d.jsonl", samples)
        all_samples.extend(samples)
    qwen_records = make_qwen_sft_records(all_samples[:10])
    qwen_validation = validate_sft_records(qwen_records)
    write_jsonl(processed / "qwen25_sft_train.jsonl", qwen_records[:8])
    write_jsonl(processed / "qwen25_sft_val.jsonl", qwen_records[8:9])
    write_jsonl(processed / "qwen25_sft_test.jsonl", qwen_records[9:])
    write_table(processed / "ebola_cases_daily.parquet", cases)
    write_table(processed / "weather_features.parquet", weather)
    write_table(processed / "mobility_features.parquet", mobility)
    write_table(processed / "ebola_quant_panel.parquet", panel)
    baseline_metrics = train_and_evaluate([s for s in all_samples if s["horizon_days"] == 14])
    backtest_metrics = walk_forward_backtest([s for s in all_samples if s["horizon_days"] == 14], min_train=10)
    api_result = predict_trend(
        {
            "country": "Sierra Leone",
            "adm1": "Eastern Province",
            "date": "2014-09-01",
            "history_days": 60,
            "horizon_days": 14,
            "case_series": [{"date": r["date"], "new_cases": r["new_cases"], "cum_cases": r["cum_cases"]} for r in cases[:60]],
            "auto_fetch_weather": False,
            "auto_fetch_mobility": False,
        }
    )
    summary = {
        "synthetic": True,
        "case_records": len(cases),
        "panel_records": len(panel),
        "trend_samples": len(all_samples),
        "qwen_sft_validation": qwen_validation,
        "baseline_metrics": baseline_metrics,
        "backtest_metrics": backtest_metrics,
        "api_result": api_result,
    }
    write_json(reports / "synthetic_smoke_summary.json", summary)
    report_lines = [
        "# Synthetic Smoke Test Report",
        "",
        "This report is synthetic and validates the pipeline only. It is not real model performance.",
        "",
        f"case_records: {len(cases)}",
        f"panel_records: {len(panel)}",
        f"trend_samples: {len(all_samples)}",
        f"qwen_sft_valid: {qwen_validation['valid']}",
        f"api_trend: {api_result['trend']}",
        "",
        "Real short-horizon training still requires validated daily or weekly Ebola case data.",
    ]
    (reports / "smoke_test_report.md").write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    (reports / "data_quality_report.md").write_text(
        "# Data Quality Report\n\nSynthetic smoke data was used. Real training remains blocked until daily or weekly case data is provided.\n",
        encoding="utf-8",
    )
    return summary


def main() -> int:
    summary = run_smoke()
    print(json.dumps({"status": "ok", "synthetic": True, "trend_samples": summary["trend_samples"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
