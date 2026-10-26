from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from typing import Any

from src.eval.metrics import LABEL_DOWN, LABEL_ORDER, LABEL_STABLE, LABEL_UP
from src.utils.config import load_config, resolve_path
from src.utils.io import read_table, write_json, write_jsonl


PREDICTION_TIME_CONVENTION = (
    "Prediction is made at the end of date D. Features may use data up to and including D. "
    "The future label window starts at D+1."
)

TREND_UP = LABEL_UP
TREND_DOWN = LABEL_DOWN
TREND_STABLE = LABEL_STABLE


def label_growth(growth: float, up_threshold: float = 0.2, down_threshold: float = -0.2) -> str:
    if growth >= up_threshold:
        return TREND_UP
    if growth <= down_threshold:
        return TREND_DOWN
    return TREND_STABLE


def build_labels(
    panel: list[dict],
    horizon_days: int,
    past_window_days: int = 14,
    up_threshold: float = 0.2,
    down_threshold: float = -0.2,
) -> list[dict]:
    """Build labels for predictions made at the end of date D.

    For each sample, feature values come from the panel row at D and rolling
    history through D. The target compares the past window through D against
    the future horizon starting at D+1, so label construction must never use
    D+1..D+horizon values as model features.
    """

    grouped: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for row in panel:
        grouped[(_safe_str(row.get("country", "")), _safe_str(row.get("adm1", "")), _safe_str(row.get("adm2", "")))].append(row)
    samples: list[dict] = []
    for rows in grouped.values():
        rows.sort(key=lambda row: str(row["date"])[:10])
        for idx, row in enumerate(rows):
            past_rows = rows[max(0, idx - past_window_days + 1) : idx + 1]
            future_rows = rows[idx + 1 : idx + 1 + horizon_days]
            if len(future_rows) < horizon_days:
                continue
            past = sum(float(item.get("new_cases", 0.0) or 0.0) for item in past_rows)
            future = sum(float(item.get("new_cases", 0.0) or 0.0) for item in future_rows)
            growth = (future - past) / max(past, 1.0)
            date = str(row["date"])[:10]
            sample = {
                "sample_id": f"{row.get('country','')}_{row.get('adm1','')}_{date}_{horizon_days}".replace(" ", "_"),
                "date": date,
                "country": _safe_str(row.get("country", "")),
                "adm1": _safe_str(row.get("adm1", "")),
                "horizon_days": horizon_days,
                "prediction_time_convention": PREDICTION_TIME_CONVENTION,
                "features": {
                    "case_features": {
                        key: value
                        for key, value in row.items()
                        if key.startswith(("new_cases_", "growth_", "case_", "death_", "zero_", "reporting_"))
                    },
                    "weather_features": {
                        key: value
                        for key, value in row.items()
                        if any(token in key for token in ["temp", "humidity", "precipitation", "rainfall", "soil", "wind", "wet_bulb", "dew_point"])
                    },
                    "ecology_features": {
                        key: value
                        for key, value in row.items()
                        if any(token in key for token in ["ndvi", "evi", "forest", "land_cover", "population_density", "centroid_quality", "admin_match_confidence"])
                    },
                    "mobility_features": {
                        key: value
                        for key, value in row.items()
                        if any(token in key for token in ["flow", "mobility", "airport", "road", "inflow", "cross_border", "accessibility"])
                    },
                    "intervention_features": {
                        key: value
                        for key, value in row.items()
                        if any(token in key for token in ["intervention", "vaccination", "contact_tracing", "case_isolation", "treatment_center", "burial_policy", "travel_restriction"])
                    },
                    "external_features": {
                        key: value
                        for key, value in row.items()
                        if any(token in key for token in ["population_normalized_incidence", "case_growth_x_geo", "case_growth_x_road_access", "case_growth14_x_road_access"])
                    },
                },
                "label": label_growth(growth, up_threshold, down_threshold),
                "growth": growth,
                "past_window_sum": past,
                "future_window_sum": future,
                "source_provenance": row.get("source_provenance", {}),
                "data_quality_flags": row.get("data_quality_flags", []),
            }
            samples.append(sample)
    return sorted(samples, key=lambda item: (item["date"], item["country"], item["adm1"], item["sample_id"]))


def _safe_str(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    text = str(value)
    return "" if text.lower() == "nan" else text


def label_summary(samples: list[dict]) -> dict[str, Any]:
    counts = Counter(str(sample.get("label", "")) for sample in samples)
    return {
        "samples": len(samples),
        "label_counts": {label: counts.get(label, 0) for label in LABEL_ORDER},
        "missing_labels": [label for label in LABEL_ORDER if counts.get(label, 0) == 0],
    }


def build_and_write_labels(config_path: str, horizons: list[int]) -> dict[str, Any]:
    cfg = load_config(config_path)
    processed = resolve_path(cfg["paths"]["data_processed"])
    reports = resolve_path(cfg["paths"]["outputs"]) / "reports"
    panel = read_table(processed / "ebola_quant_panel.parquet")
    output: dict[str, Any] = {
        "status": "ok" if panel else "blocked",
        "blocked_reason": "none" if panel else "missing_feature_panel",
        "prediction_time_convention": PREDICTION_TIME_CONVENTION,
        "horizons": {},
    }
    for horizon in horizons:
        samples = build_labels(
            panel,
            horizon_days=int(horizon),
            past_window_days=int(cfg["data"]["past_window_days"]),
            up_threshold=float(cfg["data"]["up_threshold"]),
            down_threshold=float(cfg["data"]["down_threshold"]),
        )
        write_jsonl(processed / f"trend_dataset_{horizon}d.jsonl", samples)
        output["horizons"][f"{horizon}d"] = label_summary(samples)
    write_json(reports / "label_build_report.json", output)
    md = [
        "# Label Build Report",
        "",
        f"status: {output['status']}",
        f"blocked_reason: {output['blocked_reason']}",
        "",
        "## Prediction-Time Convention",
        "",
        PREDICTION_TIME_CONVENTION,
        "",
        "## Horizon Label Distributions",
        "",
        "| horizon | samples | \u4e0a\u5347 | \u4e0b\u964d | \u7a33\u5b9a | missing_labels |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for horizon, item in output["horizons"].items():
        counts = item["label_counts"]
        md.append(
            f"| {horizon} | {item['samples']} | {counts.get(LABEL_UP, 0)} | "
            f"{counts.get(LABEL_DOWN, 0)} | {counts.get(LABEL_STABLE, 0)} | "
            f"{', '.join(item['missing_labels']) or 'none'} |"
        )
    (reports / "label_build_report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Build and verify P0a trend labels.")
    parser.add_argument("--config", default="configs/config.yaml")
    parser.add_argument("--horizons", nargs="*", type=int)
    parser.add_argument("--horizon-days", type=int, help="Backward-compatible single-horizon option.")
    args = parser.parse_args()
    cfg = load_config(args.config)
    horizons = args.horizons or ([args.horizon_days] if args.horizon_days else [int(item) for item in cfg["data"].get("horizons", [7, 14, 21, 30])])
    output = build_and_write_labels(args.config, horizons)
    print(json.dumps(output, ensure_ascii=False))
    return 0 if output["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
