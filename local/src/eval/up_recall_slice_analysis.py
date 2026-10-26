from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any

from src.eval.metrics import LABEL_ORDER, LABEL_STABLE, LABEL_UP
from src.eval.p0c4a_common import qwen_samples
from src.utils.config import resolve_path
from src.utils.io import ensure_dir, write_json


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except Exception:
        return None


def _month(sample: dict[str, Any]) -> str:
    date = str(sample.get("date", ""))[:10]
    return date[:7] if len(date) >= 7 else "unknown"


def _features(sample: dict[str, Any]) -> dict[str, Any]:
    return sample.get("input_features") if isinstance(sample.get("input_features"), dict) else {}


def _case_features(sample: dict[str, Any]) -> dict[str, Any]:
    return _features(sample).get("case_features") or {}


def evidence_has_case_growth(sample: dict[str, Any]) -> bool:
    evidence = sample.get("evidence")
    text = " ".join(str(item) for item in evidence) if isinstance(evidence, list) else str(evidence or "")
    lowered = text.lower()
    keywords = ["case", "growth", "increase", "新增", "病例", "增长", "上升"]
    return any(keyword in lowered for keyword in keywords)


def bucketize(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "mean": None, "min": None, "max": None}
    return {"count": len(values), "mean": mean(values), "min": min(values), "max": max(values)}


def analyze_slices(samples: list[dict[str, Any]]) -> dict[str, Any]:
    up = [row for row in samples if row.get("true_label") == LABEL_UP]
    up_as_stable = [row for row in up if row.get("predicted_label") == LABEL_STABLE]
    up_as_down = [row for row in up if row.get("predicted_label") == LABEL_ORDER[1]]
    counters = {
        "horizon": Counter(str(row.get("horizon_days")) for row in up_as_stable),
        "country": Counter(str(row.get("country") or "unknown") for row in up_as_stable),
        "month": Counter(_month(row) for row in up_as_stable),
        "data_quality_flag": Counter(flag for row in up_as_stable for flag in (row.get("data_quality_flags") or ["none"])),
    }
    geo_proxy = Counter()
    evidence_counter = Counter()
    confidence_values: list[float] = []
    risk_values: list[float] = []
    growth_values: dict[str, list[float]] = defaultdict(list)
    weather_values: dict[str, list[float]] = defaultdict(list)
    mobility_values: dict[str, list[float]] = defaultdict(list)
    for row in up_as_stable:
        flags = set(row.get("data_quality_flags") or [])
        geo_proxy["country_centroid"] += int("country_centroid_lat_lon_proxy" in flags)
        geo_proxy["missing_lat_lon"] += int("missing_lat_lon" in flags or "lat_lon_missing" in flags)
        evidence_counter["case_growth_evidence_present"] += int(evidence_has_case_growth(row))
        evidence_counter["case_growth_evidence_missing"] += int(not evidence_has_case_growth(row))
        if _num(row.get("confidence")) is not None:
            confidence_values.append(float(row["confidence"]))
        if _num(row.get("risk_score")) is not None:
            risk_values.append(float(row["risk_score"]))
        case = _case_features(row)
        for key in ["growth_7d", "growth_14d", "growth_28d", "case_acceleration", "new_cases_7d_sum", "new_cases_14d_sum"]:
            value = _num(case.get(key))
            if value is not None:
                growth_values[key].append(value)
        weather = _features(row).get("weather_features") or {}
        for key in ["precipitation_sum", "relative_humidity_2m_mean", "temperature_2m_mean", "temp_mean_7d"]:
            value = _num(weather.get(key))
            if value is not None:
                weather_values[key].append(value)
        mobility = _features(row).get("mobility_features") or {}
        for key in ["infectious_inflow_index", "mobility_pressure_index"]:
            value = _num(mobility.get(key))
            if value is not None:
                mobility_values[key].append(value)
    return {
        "total_samples": len(samples),
        "up_support": len(up),
        "up_as_stable_count": len(up_as_stable),
        "up_as_down_count": len(up_as_down),
        "up_as_stable_rate_among_up": len(up_as_stable) / len(up) if up else 0.0,
        "top_horizons": dict(counters["horizon"].most_common()),
        "top_countries": dict(counters["country"].most_common(10)),
        "top_months": dict(counters["month"].most_common(10)),
        "data_quality_flags": dict(counters["data_quality_flag"].most_common()),
        "geo_quality": dict(geo_proxy),
        "case_growth_evidence": dict(evidence_counter),
        "confidence": bucketize(confidence_values),
        "risk_score": bucketize(risk_values),
        "case_growth_features": {key: bucketize(values) for key, values in growth_values.items()},
        "weather_features": {key: bucketize(values) for key, values in weather_values.items()},
        "mobility_features": {key: bucketize(values) for key, values in mobility_values.items()},
        "answers": {
            "main_horizon_misses": dict(counters["horizon"].most_common(4)),
            "main_country_or_period_misses": {
                "countries": dict(counters["country"].most_common(5)),
                "months": dict(counters["month"].most_common(5)),
            },
            "low_case_base_concentration": "review case_growth_features/new_cases summaries",
            "geo_precision_related": bool(geo_proxy.get("country_centroid") or geo_proxy.get("missing_lat_lon")),
            "evidence_insufficient_related": evidence_counter.get("case_growth_evidence_missing", 0) > evidence_counter.get("case_growth_evidence_present", 0),
            "qwen_stable_bias_related": len(up_as_stable) > len(up_as_down),
        },
    }


def write_figure(result: dict[str, Any], path: Path) -> None:
    ensure_dir(path.parent)
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        horizons = list(result["top_horizons"].keys())
        values = [result["top_horizons"][key] for key in horizons]
        fig, ax = plt.subplots(figsize=(7, 4))
        ax.bar(horizons, values, color="#d62728")
        ax.set_title("Up -> Stable Misses by Horizon")
        ax.set_xlabel("horizon_days")
        ax.set_ylabel("count")
        fig.tight_layout()
        fig.savefig(path, dpi=150)
        plt.close(fig)
    except Exception:
        path.with_suffix(".txt").write_text(json.dumps(result["top_horizons"], ensure_ascii=False, indent=2), encoding="utf-8")


def run_analysis() -> dict[str, Any]:
    try:
        samples = qwen_samples(required=True)
    except FileNotFoundError as exc:
        return {"status": "blocked", "blocked_reason": str(exc), "missing_artifacts": ["qwen_lora_eval_samples.jsonl"]}
    result = analyze_slices(samples)
    result.update({"status": "ok", "blocked_reason": "none", "artifact_source": "data/outputs/reports/qwen_lora_eval_samples.jsonl"})
    figure = resolve_path("data/outputs/figures/up_misclassification_slices.png")
    write_figure(result, figure)
    result["figure"] = "data/outputs/figures/up_misclassification_slices.png"
    return result


def write_report(result: dict[str, Any]) -> Path:
    reports = ensure_dir(resolve_path("data/outputs/reports"))
    write_json(reports / "up_recall_slice_analysis.json", result)
    lines = [
        "# Up Recall Slice Analysis",
        "",
        f"status: {result['status']}",
        f"blocked_reason: {result['blocked_reason']}",
        f"up_support: {result.get('up_support')}",
        f"up_as_stable_count: {result.get('up_as_stable_count')}",
        f"up_as_stable_rate_among_up: {result.get('up_as_stable_rate_among_up')}",
        "",
        "## Answers",
        "```json",
        json.dumps(result.get("answers", {}), ensure_ascii=False, indent=2, sort_keys=True),
        "```",
        "",
        "## Slice Details",
        "```json",
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True),
        "```",
    ]
    path = reports / "up_recall_slice_analysis.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze up-class recall misses for P0c-4A.")
    parser.parse_args()
    result = run_analysis()
    report = write_report(result)
    print(json.dumps({"status": result["status"], "blocked_reason": result["blocked_reason"], "report": report.as_posix()}, ensure_ascii=False))
    return 0 if result["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
