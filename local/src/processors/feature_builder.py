from __future__ import annotations

import argparse
from collections import defaultdict
import math

from src.utils.config import load_config, resolve_path
from src.utils.io import read_table, write_json, write_table


FEATURE_GROUP_PREFIXES = {
    "cases_only": ("new_cases_", "growth_", "case_", "death_", "zero_", "reporting_"),
    "cases_weather": (
        "new_cases_",
        "growth_",
        "case_",
        "death_",
        "zero_",
        "reporting_",
        "temp",
        "humidity",
        "precipitation",
        "soil",
        "wind",
        "wet_bulb",
        "dew_point",
    ),
    "cases_mobility_proxy": (
        "new_cases_",
        "growth_",
        "case_",
        "death_",
        "zero_",
        "reporting_",
        "flow",
        "mobility",
        "airport",
        "road",
        "inflow",
    ),
    "cases_weather_mobility_proxy": (
        "new_cases_",
        "growth_",
        "case_",
        "death_",
        "zero_",
        "reporting_",
        "temp",
        "humidity",
        "precipitation",
        "soil",
        "wind",
        "wet_bulb",
        "dew_point",
        "flow",
        "mobility",
        "airport",
        "road",
        "inflow",
    ),
}


def build_case_features(case_records: list[dict]) -> list[dict]:
    grouped: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for record in case_records:
        grouped[(_safe_str(record.get("country", "")), _safe_str(record.get("adm1", "")), _safe_str(record.get("adm2", "")))].append(record)
    output = []
    for rows in grouped.values():
        rows.sort(key=lambda r: r["date"])
        for idx, row in enumerate(rows):
            enriched = dict(row)
            for window in (7, 14, 28):
                lookback = rows[max(0, idx - window + 1) : idx + 1]
                total = sum(float(r.get("new_cases", 0.0) or 0.0) for r in lookback)
                enriched[f"new_cases_{window}d_sum"] = total
                enriched[f"new_cases_{window}d_mean"] = total / max(len(lookback), 1)
                previous = rows[max(0, idx - 2 * window + 1) : max(0, idx - window + 1)]
                prev_total = sum(float(r.get("new_cases", 0.0) or 0.0) for r in previous)
                enriched[f"growth_{window}d"] = (total - prev_total) / max(prev_total, 1.0)
            enriched["case_acceleration"] = enriched["growth_7d"] - enriched["growth_14d"]
            deaths = float(row.get("deaths", 0.0) or 0.0)
            cum = max(float(row.get("cum_cases", 0.0) or 0.0), 1.0)
            enriched["death_case_ratio"] = deaths / cum
            output.append(enriched)
    return sorted(output, key=lambda r: (r["date"], r.get("country", ""), r.get("adm1", "")))


def join_features(case_features: list[dict], weather_features: list[dict] | None = None, mobility_features: list[dict] | None = None) -> list[dict]:
    weather_lookup = {(str(r.get("date"))[:10], _safe_str(r.get("country")), _safe_str(r.get("adm1", ""))): r for r in (weather_features or [])}
    mobility_lookup = {(_safe_str(r.get("country")), _safe_str(r.get("adm1", ""))): r for r in (mobility_features or [])}
    output = []
    for row in case_features:
        key = (str(row.get("date"))[:10], _safe_str(row.get("country")), _safe_str(row.get("adm1", "")))
        region_key = (_safe_str(row.get("country")), _safe_str(row.get("adm1", "")))
        merged = dict(row)
        for source in (weather_lookup.get(key, {}), mobility_lookup.get(region_key, {})):
            for k, v in source.items():
                if k not in {"date", "country", "adm1", "adm2"}:
                    merged[k] = v
        merged.setdefault("source_provenance", {"cases": row.get("source", "unknown")})
        merged.setdefault("data_quality_flags", row.get("data_quality_flags", []))
        output.append(merged)
    return output


def _safe_str(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    text = str(value)
    return "" if text.lower() == "nan" else text


def feature_set_columns(panel: list[dict]) -> dict[str, list[str]]:
    columns = sorted({key for row in panel for key in row})
    output: dict[str, list[str]] = {}
    for feature_set, prefixes in FEATURE_GROUP_PREFIXES.items():
        output[feature_set] = [column for column in columns if column.startswith(prefixes)]
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Build quantitative Ebola panel features.")
    parser.add_argument("--config", default="configs/config.yaml")
    args = parser.parse_args()
    cfg = load_config(args.config)
    processed = resolve_path(cfg["paths"]["data_processed"])
    cases = read_table(processed / "ebola_cases_daily.parquet")
    case_features = build_case_features(cases)
    weather = read_table(processed / "weather_features.parquet")
    mobility = read_table(processed / "mobility_features.parquet")
    panel = join_features(case_features, weather, mobility)
    write_table(processed / "ebola_quant_panel.parquet", panel)
    report = {
        "status": "ok" if panel else "blocked",
        "case_records": len(cases),
        "case_feature_records": len(case_features),
        "weather_feature_records": len(weather),
        "mobility_feature_records": len(mobility),
        "panel_records": len(panel),
        "feature_set_columns": feature_set_columns(panel),
        "data_quality_flags": [
            flag
            for flag, missing in [
                ("weather_features_unavailable", not weather),
                ("mobility_proxy_unavailable", not mobility),
            ]
            if missing
        ],
    }
    reports = resolve_path(cfg["paths"]["outputs"]) / "reports"
    write_json(reports / "feature_build_report.json", report)
    md = [
        "# Feature Build Report",
        "",
        f"status: {report['status']}",
        f"case_records: {len(cases)}",
        f"weather_feature_records: {len(weather)}",
        f"mobility_feature_records: {len(mobility)}",
        f"panel_records: {len(panel)}",
        "",
        "## Feature Sets",
    ]
    for feature_set, columns in report["feature_set_columns"].items():
        md.append(f"- {feature_set}: {len(columns)} columns")
    (reports / "feature_build_report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"panel_records={len(panel)}")
    return 0 if panel else 2


if __name__ == "__main__":
    raise SystemExit(main())
