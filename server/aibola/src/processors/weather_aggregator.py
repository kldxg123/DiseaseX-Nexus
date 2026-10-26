from __future__ import annotations

import argparse
import math
from collections import defaultdict

from src.utils.config import load_config, resolve_path
from src.utils.io import read_table, write_json, write_table


def calculate_absolute_humidity(temp_c: float, relative_humidity: float) -> float:
    """Approximate absolute humidity in g/m^3 from Celsius and RH percent."""
    saturation = 6.112 * math.exp((17.67 * temp_c) / (temp_c + 243.5))
    vapor_pressure = (relative_humidity / 100.0) * saturation
    return 2.1674 * vapor_pressure / (273.15 + temp_c) * 100.0


def rolling_weather_features(records: list[dict], windows: tuple[int, ...] = (7, 14, 30)) -> list[dict]:
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for record in records:
        grouped[(record.get("country", ""), record.get("adm1", ""))].append(record)
    output: list[dict] = []
    for key, rows in grouped.items():
        rows.sort(key=lambda r: r["date"])
        for idx, row in enumerate(rows):
            enriched = dict(row)
            if "temperature_2m_mean" in row and "relative_humidity_2m_mean" in row:
                enriched["absolute_humidity"] = calculate_absolute_humidity(
                    float(row.get("temperature_2m_mean") or 0.0),
                    float(row.get("relative_humidity_2m_mean") or 0.0),
                )
            for window in windows:
                lookback = rows[max(0, idx - window + 1) : idx + 1]
                enriched[f"temp_mean_{window}d"] = _mean(lookback, "temperature_2m_mean")
                enriched[f"rh_mean_{window}d"] = _mean(lookback, "relative_humidity_2m_mean")
                enriched[f"precipitation_sum_{window}d"] = _sum(lookback, "precipitation_sum")
                enriched[f"absolute_humidity_{window}d"] = _mean(lookback, "absolute_humidity", enriched)
            output.append(enriched)
    return output


def _mean(rows: list[dict], key: str, current: dict | None = None) -> float:
    values = []
    for row in rows:
        value = row.get(key)
        if value is None and current and row is rows[-1]:
            value = current.get(key)
        try:
            values.append(float(value))
        except (TypeError, ValueError):
            pass
    return sum(values) / len(values) if values else 0.0


def _sum(rows: list[dict], key: str) -> float:
    total = 0.0
    for row in rows:
        try:
            total += float(row.get(key, 0.0) or 0.0)
        except (TypeError, ValueError):
            pass
    return total


def main() -> int:
    parser = argparse.ArgumentParser(description="Aggregate daily weather features.")
    parser.add_argument("--config", default="configs/config.yaml")
    args = parser.parse_args()
    cfg = load_config(args.config)
    in_path = resolve_path(cfg["paths"]["data_processed"]) / "weather_daily.parquet"
    records = read_table(in_path)
    features = rolling_weather_features(records)
    write_table(resolve_path(cfg["paths"]["data_processed"]) / "weather_features.parquet", features)
    write_json(
        resolve_path(cfg["paths"]["outputs"]) / "reports" / "weather_aggregation_report.json",
        {
            "input_records": len(records),
            "feature_records": len(features),
            "status": "ok" if features else "empty",
            "data_quality_flag": "" if features else "weather_features_unavailable",
        },
    )
    print(f"weather_feature_records={len(features)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
