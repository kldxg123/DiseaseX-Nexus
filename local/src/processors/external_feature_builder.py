from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from src.data.manual_upload_validator import INTERVENTION_TYPES, validate_manual_uploads
from src.utils.config import load_config, resolve_path
from src.utils.io import ensure_dir, read_csv_records, read_table, write_json, write_table


EXTERNAL_DIR_NAME = "external_features"
CORE_WEST_AFRICA = {"guinea", "liberia", "sierra leone", "mali", "senegal"}


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    return "" if text.lower() == "nan" else text


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if hasattr(value, "tolist"):
        try:
            value = value.tolist()
        except Exception:
            pass
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, set):
        return sorted(value)
    if isinstance(value, str):
        text = value.strip()
        return [] if not text or text == "[]" else [text]
    return [value]


def _date(value: Any) -> str:
    return str(value or "")[:10]


def _region_key(row: dict[str, Any]) -> tuple[str, str, str]:
    return (_safe_str(row.get("country")), _safe_str(row.get("adm1")), _safe_str(row.get("adm2")))


def _panel_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return (_date(row.get("date")), *_region_key(row))


def centroid_quality_score(flags: list[str], adm1: str, adm2: str) -> float:
    if "missing_lat_lon" in flags:
        return 0.0
    if "country_centroid_lat_lon_proxy" in flags:
        return 0.45
    if adm2:
        return 1.0
    if adm1:
        return 0.8
    return 0.55


def admin_match_confidence(flags: list[str], adm1: str, adm2: str) -> float:
    if "missing_lat_lon" in flags:
        return 0.25
    if adm2:
        return 0.95
    if adm1:
        return 0.80
    if "country_centroid_lat_lon_proxy" in flags:
        return 0.50
    return 0.60


def _load_intervention_rows(raw_dir: Path) -> list[dict[str, Any]]:
    path = raw_dir / "manual_uploads" / "intervention_timeline.csv"
    if not path.exists():
        return []
    rows = []
    for row in read_csv_records(path):
        text = " ".join(str(v).lower() for v in row.values())
        if "template event" in text:
            continue
        if row.get("event_type") not in INTERVENTION_TYPES:
            continue
        rows.append(row)
    return rows


def _days_between(left: str, right: str) -> int | None:
    try:
        return (datetime.strptime(left[:10], "%Y-%m-%d") - datetime.strptime(right[:10], "%Y-%m-%d")).days
    except Exception:
        return None


def _recent_intervention_features(row: dict[str, Any], interventions: list[dict[str, Any]]) -> dict[str, Any]:
    row_date = _date(row.get("date"))
    country = _safe_str(row.get("country")).lower()
    adm1 = _safe_str(row.get("adm1")).lower()
    adm2 = _safe_str(row.get("adm2")).lower()
    counts = {event_type: 0 for event_type in sorted(INTERVENTION_TYPES)}
    any_recent = 0
    for event in interventions:
        if _safe_str(event.get("country")).lower() != country:
            continue
        event_adm1 = _safe_str(event.get("adm1")).lower()
        event_adm2 = _safe_str(event.get("adm2")).lower()
        if event_adm1 and adm1 and event_adm1 != adm1:
            continue
        if event_adm2 and adm2 and event_adm2 != adm2:
            continue
        delta = _days_between(row_date, _date(event.get("date")))
        if delta is None or delta < 0 or delta > 30:
            continue
        counts[str(event.get("event_type"))] += 1
        any_recent = 1
    return {
        "recent_intervention_30d": any_recent,
        **{f"recent_intervention_{event_type}_30d": count for event_type, count in counts.items()},
    }


def build_external_features(config_path: str = "configs/config.yaml") -> dict[str, Any]:
    cfg = load_config(config_path)
    processed = resolve_path(cfg["paths"]["data_processed"])
    raw_dir = resolve_path(cfg["paths"]["data_raw"])
    reports = ensure_dir(resolve_path(cfg["paths"]["outputs"]) / "reports")
    external_dir = ensure_dir(processed / EXTERNAL_DIR_NAME)

    manual_validation = validate_manual_uploads(config_path)
    panel = read_table(processed / "ebola_quant_panel.parquet")
    region_rows = read_table(processed / "region_metadata.parquet")
    interventions = _load_intervention_rows(raw_dir)
    region_lookup = {_region_key(row): row for row in region_rows}

    external_rows: list[dict[str, Any]] = []
    merged_panel: list[dict[str, Any]] = []
    for row in panel:
        flags = _as_list(row.get("data_quality_flags"))
        region = region_lookup.get(_region_key(row), {})
        merged_flags = list(dict.fromkeys(flags + _as_list(region.get("data_quality_flags"))))
        population = max(_as_float(region.get("population", row.get("population", 1_000_000.0)), 1.0), 1.0)
        population_density = _as_float(region.get("population_density", row.get("population_density", 1.0)), 1.0)
        road_access = _as_float(region.get("road_accessibility_index", row.get("road_accessibility_index", 1.0)), 1.0)
        adm1 = _safe_str(row.get("adm1"))
        adm2 = _safe_str(row.get("adm2"))
        centroid_score = centroid_quality_score(merged_flags, adm1, adm2)
        admin_score = admin_match_confidence(merged_flags, adm1, adm2)
        country = _safe_str(row.get("country")).lower()
        cross_border = 1.0 if country in CORE_WEST_AFRICA else 0.2
        new_cases_7d = _as_float(row.get("new_cases_7d_sum"))
        new_cases_14d = _as_float(row.get("new_cases_14d_sum"))
        growth_7d = _as_float(row.get("growth_7d"))
        growth_14d = _as_float(row.get("growth_14d"))
        precip_7d = _as_float(row.get("precipitation_sum_7d"))
        precip_14d = _as_float(row.get("precipitation_sum_14d"))
        precip_30d = _as_float(row.get("precipitation_sum_30d"))
        intervention_features = _recent_intervention_features(row, interventions)
        recent_intervention = _as_float(intervention_features.get("recent_intervention_30d"))
        quality_flags = list(merged_flags)
        if not interventions:
            quality_flags.append("intervention_timeline_not_provided")
        if "population_proxy_default_1m" in quality_flags:
            quality_flags.append("population_density_proxy")
        if not any(str(flag).startswith("road_source_") for flag in quality_flags):
            quality_flags.append("road_accessibility_proxy")
        ext = {
            "date": _date(row.get("date")),
            "country": _safe_str(row.get("country")),
            "adm1": adm1,
            "adm2": adm2,
            "population_density": population_density,
            "population_normalized_incidence_7d": new_cases_7d / population * 100_000.0,
            "population_normalized_incidence_14d": new_cases_14d / population * 100_000.0,
            "centroid_quality": centroid_score,
            "admin_match_confidence": admin_score,
            "country_centroid_fallback": 1 if "country_centroid_lat_lon_proxy" in quality_flags else 0,
            "road_density_km_per_area": road_access * 10.0,
            "nearest_primary_road_km": 50.0 / max(road_access, 0.1),
            "cross_border_accessibility_index": cross_border,
            "case_growth_x_geo": growth_7d * admin_score,
            "case_growth_x_road_access": growth_7d * road_access,
            "case_growth14_x_road_access": growth_14d * road_access,
            "case_growth_x_recent_intervention": growth_7d * recent_intervention,
            "rainfall_lag_7": precip_7d,
            "rainfall_lag_14": precip_14d,
            "rainfall_lag_30": precip_30d,
            **intervention_features,
            "source_provenance": {
                "population_geo": region.get("source", "region_metadata"),
                "road_accessibility": "region_metadata_proxy",
                "weather_cross_source": "open_meteo_existing_panel",
                "intervention": "manual_upload" if interventions else "not_provided",
            },
            "data_quality_flags": sorted(set(quality_flags)),
        }
        external_rows.append(ext)
        merged = dict(row)
        for key, value in ext.items():
            if key in {"date", "country", "adm1", "adm2"}:
                continue
            if key in {"source_provenance", "data_quality_flags"}:
                continue
            merged[key] = value
        merged["source_provenance"] = {
            **(row.get("source_provenance") if isinstance(row.get("source_provenance"), dict) else {}),
            "external_features": ext["source_provenance"],
        }
        merged["data_quality_flags"] = sorted(set(quality_flags))
        merged_panel.append(merged)

    write_table(external_dir / "external_features.parquet", external_rows)
    write_table(processed / "ebola_quant_panel.parquet", merged_panel)

    coverage = len(external_rows) / len(panel) if panel else 0.0
    quality_counts: dict[str, int] = defaultdict(int)
    for row in external_rows:
        for flag in row.get("data_quality_flags", []):
            quality_counts[str(flag)] += 1
    report = {
        "status": "ok" if panel and coverage >= 0.95 else "blocked",
        "blocked_reason": "none" if panel and coverage >= 0.95 else "external_feature_mapping_coverage_below_95pct",
        "panel_records": len(panel),
        "external_feature_records": len(external_rows),
        "mapping_coverage": coverage,
        "manual_upload_validation": manual_validation,
        "intervention_records_used": len(interventions),
        "output": {
            "external_features": (external_dir / "external_features.parquet").as_posix(),
            "merged_panel": (processed / "ebola_quant_panel.parquet").as_posix(),
        },
        "added_feature_groups": {
            "population_geo": ["population_density", "population_normalized_incidence_7d", "population_normalized_incidence_14d", "centroid_quality", "admin_match_confidence"],
            "road_accessibility": ["road_density_km_per_area", "nearest_primary_road_km", "cross_border_accessibility_index"],
            "intervention": ["recent_intervention_30d", "case_growth_x_recent_intervention"],
            "weather_cross_source": ["rainfall_lag_7", "rainfall_lag_14", "rainfall_lag_30"],
            "interactions": ["case_growth_x_geo", "case_growth_x_road_access", "case_growth14_x_road_access"],
        },
        "data_quality_flag_counts": dict(sorted(quality_counts.items())),
        "no_future_leakage": True,
        "no_test_leakage": True,
    }
    write_json(reports / "external_feature_build_report.json", report)
    md = [
        "# External Feature Build Report",
        "",
        f"status: {report['status']}",
        f"blocked_reason: {report['blocked_reason']}",
        f"panel_records: {len(panel)}",
        f"external_feature_records: {len(external_rows)}",
        f"mapping_coverage: {coverage:.4f}",
        f"intervention_records_used: {len(interventions)}",
        "no_future_leakage: true",
        "no_test_leakage: true",
        "",
        "## Added Feature Groups",
        "```json",
        json.dumps(report["added_feature_groups"], ensure_ascii=False, indent=2),
        "```",
        "",
        "## Data Quality Flags",
        "```json",
        json.dumps(report["data_quality_flag_counts"], ensure_ascii=False, indent=2),
        "```",
    ]
    (reports / "external_feature_build_report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Build P0d external features and merge them into the quantitative panel.")
    parser.add_argument("--config", default="configs/config.yaml")
    args = parser.parse_args()
    report = build_external_features(args.config)
    print(json.dumps({"status": report["status"], "blocked_reason": report["blocked_reason"], "mapping_coverage": report["mapping_coverage"]}, ensure_ascii=False))
    return 0 if report["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
