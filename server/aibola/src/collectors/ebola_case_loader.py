from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Iterable

from src.utils.config import load_config, resolve_path
from src.utils.io import read_csv_records, write_json, write_table
from src.utils.time import parse_date


DATE_CANDIDATES = ["date", "report_date", "day", "datetime", "time"]
COUNTRY_CANDIDATES = ["country", "country_name", "location", "admin0", "adm0"]
ADM1_CANDIDATES = ["adm1", "province", "state", "region", "admin1"]
ADM2_CANDIDATES = ["adm2", "district", "county", "admin2"]
LAT_CANDIDATES = ["lat", "latitude"]
LON_CANDIDATES = ["lon", "longitude", "lng"]
CONFIRMED_CANDIDATES = ["confirmed", "cases_confirmed", "confirmed_cases"]
NEW_CASES_CANDIDATES = ["new_cases", "new_case", "incidence", "cases_new"]
CUM_CASES_CANDIDATES = ["cum_cases", "cumulative_cases", "cases", "confirmed_total", "total_cases"]
DEATHS_CANDIDATES = ["deaths", "death", "cum_deaths"]

COUNTRY_CENTROIDS = {
    "Guinea": (9.9456, -9.6966, "GIN"),
    "Liberia": (6.4281, -9.4295, "LBR"),
    "Sierra Leone": (8.4606, -11.7799, "SLE"),
    "Nigeria": (9.0820, 8.6753, "NGA"),
    "Senegal": (14.4974, -14.4524, "SEN"),
    "Mali": (17.5707, -3.9962, "MLI"),
    "United States of America": (37.0902, -95.7129, "USA"),
    "Spain": (40.4637, -3.7492, "ESP"),
    "United Kingdom": (55.3781, -3.4360, "GBR"),
    "Italy": (41.8719, 12.5674, "ITA"),
}


def _pick_column(columns: Iterable[str], candidates: list[str]) -> str | None:
    lower_map = {col.lower().strip(): col for col in columns}
    for candidate in candidates:
        if candidate in lower_map:
            return lower_map[candidate]
    for candidate in candidates:
        for lowered, original in lower_map.items():
            if candidate in lowered:
                return original
    return None


def _to_float(value: object, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def detect_granularity(records: list[dict]) -> str:
    dates = sorted({parse_date(r["date"]) for r in records if r.get("date")})
    if len(dates) < 2:
        return "unknown"
    gaps = sorted((dates[i] - dates[i - 1]).days for i in range(1, len(dates)))
    median_gap = gaps[len(gaps) // 2]
    if median_gap <= 2:
        return "daily"
    if median_gap <= 10:
        return "weekly"
    if median_gap >= 300:
        return "annual"
    return "coarse"


def normalize_case_records(rows: list[dict], source: str) -> list[dict]:
    if not rows:
        return []
    if _looks_like_indicator_long_table(rows[0].keys()):
        return normalize_indicator_long_records(rows, source)
    columns = rows[0].keys()
    date_col = _pick_column(columns, DATE_CANDIDATES)
    country_col = _pick_column(columns, COUNTRY_CANDIDATES)
    if not date_col or not country_col:
        raise ValueError(f"Could not detect required date/country columns in {source}.")
    adm1_col = _pick_column(columns, ADM1_CANDIDATES)
    adm2_col = _pick_column(columns, ADM2_CANDIDATES)
    lat_col = _pick_column(columns, LAT_CANDIDATES)
    lon_col = _pick_column(columns, LON_CANDIDATES)
    confirmed_col = _pick_column(columns, CONFIRMED_CANDIDATES)
    new_col = _pick_column(columns, NEW_CASES_CANDIDATES)
    cum_col = _pick_column(columns, CUM_CASES_CANDIDATES)
    deaths_col = _pick_column(columns, DEATHS_CANDIDATES)
    records = []
    for row in rows:
        try:
            d = parse_date(str(row.get(date_col, ""))).isoformat()
        except Exception:
            continue
        country = str(row.get(country_col, "")).strip()
        if not country:
            continue
        cum = _to_float(row.get(cum_col)) if cum_col else _to_float(row.get(confirmed_col))
        new = _to_float(row.get(new_col), default=float("nan")) if new_col else float("nan")
        records.append(
            {
                "date": d,
                "country": country,
                "iso3": str(row.get("iso3", row.get("ISO3", ""))).strip(),
                "adm1": str(row.get(adm1_col, "")).strip() if adm1_col else "",
                "adm2": str(row.get(adm2_col, "")).strip() if adm2_col else "",
                "lat": _to_float(row.get(lat_col), 0.0) if lat_col else 0.0,
                "lon": _to_float(row.get(lon_col), 0.0) if lon_col else 0.0,
                "confirmed": _to_float(row.get(confirmed_col), cum) if confirmed_col else cum,
                "probable": _to_float(row.get("probable"), 0.0),
                "suspected": _to_float(row.get("suspected"), 0.0),
                "deaths": _to_float(row.get(deaths_col), 0.0) if deaths_col else 0.0,
                "new_cases": new,
                "cum_cases": cum,
                "source": source,
                "data_quality_flags": [],
            }
        )
    return fill_new_cases(records)


def _looks_like_indicator_long_table(columns: Iterable[str]) -> bool:
    lowered = {col.lower().strip() for col in columns}
    return {"indicator", "country", "date", "value"}.issubset(lowered)


def normalize_indicator_long_records(rows: list[dict], source: str) -> list[dict]:
    records_by_key: dict[tuple[str, str], dict] = {}
    for row in rows:
        country = str(row.get("Country", row.get("country", ""))).strip()
        if not country:
            continue
        try:
            d = parse_date(str(row.get("Date", row.get("date", "")))).isoformat()
        except Exception:
            continue
        indicator = str(row.get("Indicator", row.get("indicator", ""))).strip().lower()
        value = _to_float(row.get("value", row.get("Value")), 0.0)
        lat, lon, iso3 = COUNTRY_CENTROIDS.get(country, (0.0, 0.0, ""))
        record = records_by_key.setdefault(
            (country, d),
            {
                "date": d,
                "country": country,
                "iso3": iso3,
                "adm1": "",
                "adm2": "",
                "lat": lat,
                "lon": lon,
                "confirmed": 0.0,
                "probable": 0.0,
                "suspected": 0.0,
                "deaths": 0.0,
                "new_cases": float("nan"),
                "cum_cases": 0.0,
                "source": source,
                "data_quality_flags": ["country_centroid_lat_lon_proxy"] if lat and lon else ["missing_lat_lon"],
            },
        )
        if "cumulative number of confirmed, probable and suspected ebola cases" in indicator:
            record["cum_cases"] = value
        elif "cumulative number of confirmed ebola cases" in indicator:
            record["confirmed"] = value
        elif "cumulative number of probable ebola cases" in indicator:
            record["probable"] = value
        elif "cumulative number of suspected ebola cases" in indicator:
            record["suspected"] = value
        elif "cumulative number of confirmed, probable and suspected ebola deaths" in indicator:
            record["deaths"] = value
    records = list(records_by_key.values())
    for record in records:
        if not record["cum_cases"]:
            record["cum_cases"] = record["confirmed"] + record["probable"] + record["suspected"]
    return fill_new_cases(records)


def fill_new_cases(records: list[dict]) -> list[dict]:
    grouped: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for record in records:
        grouped[(record["country"], record.get("adm1", ""), record.get("adm2", ""))].append(record)
    output: list[dict] = []
    for group_records in grouped.values():
        group_records.sort(key=lambda r: r["date"])
        previous_cum: float | None = None
        for record in group_records:
            new_cases = record.get("new_cases")
            needs_diff = not isinstance(new_cases, (int, float)) or new_cases != new_cases
            if needs_diff:
                if previous_cum is None:
                    record["new_cases"] = max(float(record.get("cum_cases", 0.0)), 0.0)
                else:
                    diff = float(record.get("cum_cases", 0.0)) - previous_cum
                    record["new_cases"] = diff
                    if diff < 0:
                        record["data_quality_flags"].append("negative_new_cases_from_cum_diff")
            if float(record["new_cases"]) < 0 and "negative_new_cases" not in record["data_quality_flags"]:
                record["data_quality_flags"].append("negative_new_cases")
            previous_cum = float(record.get("cum_cases", previous_cum or 0.0))
            output.append(record)
    return sorted(output, key=lambda r: (r["date"], r["country"], r.get("adm1", "")))


def load_case_files(paths: list[str | Path]) -> tuple[list[dict], list[dict]]:
    all_records: list[dict] = []
    reports: list[dict] = []
    for path in paths:
        p = Path(path)
        if not p.exists():
            reports.append({"path": str(path), "status": "missing"})
            continue
        try:
            rows = read_csv_records(p)
            records = normalize_case_records(rows, source=str(p))
            granularity = detect_granularity(records)
            reports.append({"path": str(p), "status": "loaded", "rows": len(records), "granularity": granularity})
            all_records.extend(records)
        except Exception as exc:
            reports.append({"path": str(p), "status": "skipped", "error_type": type(exc).__name__, "error": str(exc)[:300]})
    return all_records, reports


def write_data_quality_report(report_path: str | Path, reports: list[dict], records: list[dict]) -> Path:
    granularity = detect_granularity(records) if records else "missing"
    can_train_short_horizon = granularity in {"daily", "weekly"}
    countries = sorted({str(record.get("country", "")).strip() for record in records if record.get("country")})
    dates = sorted(str(record.get("date", ""))[:10] for record in records if record.get("date"))
    flag_counts: dict[str, int] = defaultdict(int)
    negative_new_cases = 0
    for record in records:
        if float(record.get("new_cases", 0.0) or 0.0) < 0:
            negative_new_cases += 1
        for flag in record.get("data_quality_flags", []) or []:
            flag_counts[str(flag)] += 1
    source_granularity_counts: dict[str, int] = defaultdict(int)
    for item in reports:
        source_granularity_counts[str(item.get("granularity", item.get("status", "unknown")))] += 1
    text = [
        "# Data Quality Report",
        "",
        f"case_records: {len(records)}",
        f"detected_granularity: {granularity}",
        f"short_horizon_training_allowed: {str(can_train_short_horizon).lower()}",
        f"countries: {len(countries)}",
        f"date_min: {dates[0] if dates else 'missing'}",
        f"date_max: {dates[-1] if dates else 'missing'}",
        f"negative_new_cases_records: {negative_new_cases}",
        f"data_quality_flag_counts: {json.dumps(dict(sorted(flag_counts.items())), ensure_ascii=False)}",
        f"source_granularity_counts: {json.dumps(dict(sorted(source_granularity_counts.items())), ensure_ascii=False)}",
        "",
        "Real 7/14/21/30-day training is blocked unless daily or weekly case data is available.",
        "Annual-only files are retained only as background/context and must not train short-horizon trend labels.",
        "Negative new-case values are retained as report-revision signals and must be treated as data-quality flags.",
        "Country centroid coordinates are proxy locations, not district-level geocoding.",
        "",
        "## Sources",
    ]
    for item in reports:
        text.append(f"- {json.dumps(item, ensure_ascii=False, sort_keys=True)}")
    p = Path(report_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(text) + "\n", encoding="utf-8")
    return p


def main() -> int:
    parser = argparse.ArgumentParser(description="Normalize Ebola case CSV files.")
    parser.add_argument("--config", default="configs/config.yaml")
    parser.add_argument("--input", nargs="*")
    args = parser.parse_args()
    cfg = load_config(args.config)
    local_files = args.input or cfg["sources"]["cases"]["local_files"]
    paths = [resolve_path(path) for path in local_files]
    records, reports = load_case_files(paths)
    out_dir = resolve_path(cfg["paths"]["data_processed"])
    write_table(out_dir / "ebola_cases_daily.parquet", records)
    write_data_quality_report(resolve_path(cfg["paths"]["outputs"]) / "reports" / "data_quality_report.md", reports, records)
    print(json.dumps({"records": len(records), "sources": reports}, ensure_ascii=False))
    return 0 if records else 2


if __name__ == "__main__":
    raise SystemExit(main())
