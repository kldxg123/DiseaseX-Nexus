from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

from src.utils.config import load_config, resolve_path
from src.utils.io import ensure_dir, read_jsonl, write_json, write_table


DAILY_VARIABLES = ["temperature_2m_mean", "temperature_2m_max", "temperature_2m_min", "precipitation_sum"]
HOURLY_VARIABLES = [
    "relative_humidity_2m",
    "dew_point_2m",
    "wet_bulb_temperature_2m",
    "soil_moisture_0_to_7cm",
    "soil_moisture_7_to_28cm",
    "wind_speed_10m",
]


def aggregate_hourly_to_daily(payload: dict) -> list[dict]:
    hourly = payload.get("hourly", {})
    times = hourly.get("time", [])
    grouped: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for idx, stamp in enumerate(times):
        day = str(stamp)[:10]
        for var in HOURLY_VARIABLES:
            values = hourly.get(var, [])
            if idx < len(values) and values[idx] is not None:
                grouped[day][var].append(float(values[idx]))
    records: list[dict] = []
    for day, values in grouped.items():
        row = {"date": day}
        for var, vals in values.items():
            if not vals:
                continue
            suffix = "mean"
            row[f"{var}_{suffix}"] = sum(vals) / len(vals)
            row[f"{var}_max"] = max(vals)
            row[f"{var}_min"] = min(vals)
        records.append(row)
    return sorted(records, key=lambda r: r["date"])


def merge_daily_hourly(payload: dict, country: str = "", adm1: str = "", lat: float = 0.0, lon: float = 0.0) -> list[dict]:
    daily = payload.get("daily", {})
    rows: dict[str, dict] = {}
    for idx, day in enumerate(daily.get("time", [])):
        row = rows.setdefault(str(day), {"date": str(day)})
        for var in DAILY_VARIABLES:
            values = daily.get(var, [])
            if idx < len(values):
                row[var] = values[idx]
    for hourly_row in aggregate_hourly_to_daily(payload):
        rows.setdefault(hourly_row["date"], {"date": hourly_row["date"]}).update(hourly_row)
    output = []
    for row in sorted(rows.values(), key=lambda r: r["date"]):
        row.update({"country": country, "adm1": adm1, "lat": lat, "lon": lon, "source": "open-meteo"})
        output.append(row)
    return output


def fetch_openmeteo(lat: float, lon: float, start_date: str, end_date: str, timeout: int = 20) -> dict:
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start_date,
        "end_date": end_date,
        "daily": ",".join(DAILY_VARIABLES),
        "hourly": ",".join(HOURLY_VARIABLES),
        "timezone": "UTC",
    }
    url = "https://archive-api.open-meteo.com/v1/archive?" + urlencode(params)
    with urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def collect_weather_for_points(points: list[dict], cfg: dict) -> tuple[list[dict], list[dict]]:
    source_cfg = cfg["sources"]["openmeteo"]
    cache_dir = ensure_dir(resolve_path(source_cfg["cache_dir"]))
    rate_limit = float(source_cfg.get("rate_limit_seconds", 1))
    timeout = int(source_cfg.get("timeout_seconds", 20))
    records: list[dict] = []
    failures: list[dict] = []
    for point in points:
        lat = float(point.get("lat", 0.0))
        lon = float(point.get("lon", 0.0))
        start_date = point.get("start_date") or cfg["data"]["start_date"]
        end_date = point.get("end_date") or cfg["data"]["end_date"]
        cache_file = cache_dir / f"{lat:.4f}_{lon:.4f}_{start_date}_{end_date}.json"
        try:
            if cache_file.exists():
                payload = json.loads(cache_file.read_text(encoding="utf-8"))
            else:
                payload = fetch_openmeteo(lat, lon, start_date, end_date, timeout=timeout)
                write_json(cache_file, payload)
                time.sleep(rate_limit)
            records.extend(merge_daily_hourly(payload, point.get("country", ""), point.get("adm1", ""), lat, lon))
        except Exception as exc:
            failures.append(
                {
                    "country": point.get("country", ""),
                    "adm1": point.get("adm1", ""),
                    "lat": lat,
                    "lon": lon,
                    "start_date": start_date,
                    "end_date": end_date,
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:300],
                    "data_quality_flag": "openmeteo_fetch_failed",
                }
            )
    return records, failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect Open-Meteo daily/hourly weather and aggregate locally.")
    parser.add_argument("--config", default="configs/config.yaml")
    parser.add_argument("--points-jsonl", default="")
    args = parser.parse_args()
    cfg = load_config(args.config)
    if not args.points_jsonl:
        print(json.dumps({"status": "skipped", "reason": "no_points_jsonl"}, ensure_ascii=False))
        return 0
    points = read_jsonl(args.points_jsonl)
    records, failures = collect_weather_for_points(points, cfg)
    write_table(resolve_path(cfg["paths"]["data_processed"]) / "weather_daily.parquet", records)
    report = {
        "status": "ok" if records else "blocked",
        "records": len(records),
        "points": len(points),
        "failures": failures,
    }
    write_json(resolve_path(cfg["paths"]["outputs"]) / "reports" / "openmeteo_collection_report.json", report)
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
