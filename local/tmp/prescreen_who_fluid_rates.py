from __future__ import annotations

import csv
import json
import math
import statistics
import sys
from collections import defaultdict
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from fresh_opt_prescreen import Config, _calendarize, _configs, _hash, evaluate_block


RAW = Path("USER_HOME/projects/openclaw-fresh-ukhsa-20260814/runtime/fresh_source/who_fluid_frozen_20260814.csv")
METRICS = {
    "who_fluid_ili_rate_per_1000_outpatients": ("ILI_CASE", "ILI_OUTPATIENTS"),
    "who_fluid_sari_rate_per_1000_inpatients": ("SARI_CASE", "SARI_INPATIENTS"),
}


def number(text: str | None) -> float | None:
    value = (text or "").strip()
    if value in {"", "NA", "N/A", "-"}:
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def period(text: str | None) -> date | None:
    try:
        return datetime.strptime((text or "")[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def discover_windows() -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, str]]:
    dates: dict[tuple[str, str], set[date]] = defaultdict(set)
    names: dict[str, str] = {}
    with RAW.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if (row.get("AGEGROUP_CODE") or "").strip().casefold() != "all":
                continue
            country = (row.get("COUNTRY_CODE") or "").strip()
            current = period(row.get("ISO_WEEKSTARTDATE"))
            if not country or current is None:
                continue
            names[country] = (row.get("COUNTRY_AREA_TERRITORY") or country).strip()
            for metric, (numerator_col, denominator_col) in METRICS.items():
                numerator = number(row.get(numerator_col))
                denominator = number(row.get(denominator_col))
                if numerator is not None and numerator >= 0 and denominator is not None and denominator > 0:
                    dates[(country, metric)].add(current)
    windows: dict[tuple[str, str], dict[str, Any]] = {}
    for key, observed in dates.items():
        if not observed:
            continue
        first = min(observed)
        last = max(observed)
        calendar_weeks = ((last - first).days // 7) + 1
        hidden_start = last - timedelta(weeks=51)
        hidden_present = sum(value >= hidden_start for value in observed)
        coverage = len(observed) / calendar_weeks
        if calendar_weeks < 52 * 8 or coverage < 0.80 or hidden_present < 45:
            continue
        windows[key] = {
            "first": first,
            "last": last,
            "hidden_start": hidden_start,
            "hidden_present": hidden_present,
            "calendar_weeks": calendar_weeks,
            "coverage": coverage,
        }
    return windows, names


def load_prefixes(windows: dict[tuple[str, str], dict[str, Any]]) -> dict[tuple[str, str], dict[date, list[float]]]:
    prefixes: dict[tuple[str, str], dict[date, list[float]]] = defaultdict(lambda: defaultdict(list))
    with RAW.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if (row.get("AGEGROUP_CODE") or "").strip().casefold() != "all":
                continue
            country = (row.get("COUNTRY_CODE") or "").strip()
            current = period(row.get("ISO_WEEKSTARTDATE"))
            if not country or current is None:
                continue
            for metric, (numerator_col, denominator_col) in METRICS.items():
                key = (country, metric)
                window = windows.get(key)
                # Crucial: hidden labels are not parsed or used below.
                if window is None or current >= window["hidden_start"]:
                    continue
                numerator = number(row.get(numerator_col))
                denominator = number(row.get(denominator_col))
                if numerator is None or numerator < 0 or denominator is None or denominator <= 0:
                    continue
                prefixes[key][current].append(1000.0 * numerator / denominator)
    return prefixes


def evaluate_series(
    key: tuple[str, str],
    by_week: dict[date, list[float]],
    window: dict[str, Any],
    name: str,
    configs: list[Config],
) -> dict[str, Any]:
    country, metric = key
    conflicts = sum(1 for values in by_week.values() if max(values) - min(values) > 1e-12)
    if conflicts:
        return {"track_key": f"{country}:influenza:{metric}:week", "status": "EXCLUDED_DUPLICATE_CONFLICT"}
    rows = sorted((week, values[0]) for week, values in by_week.items())
    dates, values = _calendarize(rows)
    nonnull = sum(value is not None for value in values)
    if len(values) < 52 * 7 or nonnull / len(values) < 0.80:
        return {"track_key": f"{country}:influenza:{metric}:week", "status": "EXCLUDED_PREFIX_COVERAGE"}
    block_ends = [len(values) - 1 - 52 * offset for offset in (5, 4, 3, 2, 1, 0)]
    ranked: list[tuple[float, float, str, Config, list[dict[str, Any]]]] = []
    for config in configs:
        blocks = [evaluate_block(values, end, config) for end in block_ends[:5]]
        wapes = [block.get("wape") for block in blocks]
        if any(value is None for value in wapes) or any(block["paired_n"] < 40 for block in blocks):
            continue
        numeric = [float(value) for value in wapes if value is not None]
        ranked.append((statistics.median(numeric), max(numeric), _hash(asdict(config)), config, blocks))
    ranked.sort(key=lambda item: (item[0], item[1], item[2]))
    if not ranked:
        return {"track_key": f"{country}:influenza:{metric}:week", "status": "NO_VALID_CONFIG"}
    median_wape, worst_wape, config_sha, best_config, selection_blocks = ranked[0]
    pseudo = evaluate_block(values, block_ends[5], best_config)
    baselines: list[tuple[float, str, dict[str, Any]]] = []
    for baseline_id, baseline_config in (
        ("last_value", Config("last_value")),
        ("seasonal_naive", Config("seasonal_mean", years=1)),
    ):
        metrics = evaluate_block(values, block_ends[5], baseline_config)
        if metrics.get("wape") is not None:
            baselines.append((float(metrics["wape"]), baseline_id, metrics))
    baselines.sort(key=lambda item: item[0])
    baseline = baselines[0] if baselines else None
    pseudo_wape = pseudo.get("wape")
    improvement = None
    if baseline and pseudo_wape is not None and baseline[0] > 0:
        improvement = (baseline[0] - float(pseudo_wape)) / baseline[0]
    horizon_ok = all(
        value is None or float(value) <= 0.42 for value in (pseudo.get("per_horizon_wape") or {}).values()
    )
    pre_gate = bool(
        median_wape <= 0.25
        and worst_wape <= 0.35
        and pseudo_wape is not None
        and float(pseudo_wape) <= 0.27
        and improvement is not None
        and improvement >= 0.01
        and pseudo.get("paired_n", 0) >= 40
        and horizon_ok
    )
    return {
        "track_key": f"{country}:influenza:{metric}:week",
        "country_name": name,
        "status": "WHO_FLUID_PREFIX_PRESCREENED",
        "config": asdict(best_config),
        "config_sha256": config_sha,
        "selection_median_wape": median_wape,
        "selection_worst_wape": worst_wape,
        "selection_blocks": selection_blocks,
        "pseudo_blind": pseudo,
        "pseudo_best_baseline": None if baseline is None else {"id": baseline[1], "wape": baseline[0]},
        "pseudo_improvement_vs_baseline": improvement,
        "pre_gate": pre_gate,
        "prefix_calendar_weeks": len(values),
        "prefix_nonnull": nonnull,
        "prefix_min_period": dates[0].isoformat(),
        "prefix_max_period": dates[-1].isoformat(),
        "prefix_sha256": _hash([(d.isoformat(), v) for d, v in zip(dates, values, strict=True)]),
        "hidden_start": window["hidden_start"].isoformat(),
        "hidden_end": window["last"].isoformat(),
        "hidden_present_weeks": window["hidden_present"],
        "hidden_values_parsed": False,
        "source_sha256": "99fe45c41db35bcede7153308c4a4a3ed43369909709618bbe9bd34af567e165",
    }


def main(output_path: str) -> None:
    windows, names = discover_windows()
    prefixes = load_prefixes(windows)
    configs = _configs()
    results = [
        evaluate_series(key, prefixes[key], window, names.get(key[0], key[0]), configs)
        for key, window in sorted(windows.items())
        if key in prefixes
    ]
    eligible = [item for item in results if item.get("pre_gate")]
    eligible.sort(key=lambda item: (float(item["pseudo_blind"]["wape"]), item["track_key"]))
    payload = {
        "source": "WHO_FLUID_OFFICIAL_FROZEN",
        "source_sha256": "99fe45c41db35bcede7153308c4a4a3ed43369909709618bbe9bd34af567e165",
        "hidden_values_parsed": False,
        "candidate_count": len(results),
        "pre_gate_count": len(eligible),
        "selected_track_key": eligible[0]["track_key"] if eligible else None,
        "results": results,
    }
    Path(output_path).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "output": output_path,
                "sha256": _hash(payload),
                "candidate_count": len(results),
                "pre_gate_count": len(eligible),
                "selected_track_key": payload["selected_track_key"],
                "top_candidates": [
                    {
                        "track_key": item["track_key"],
                        "selection_median_wape": item["selection_median_wape"],
                        "selection_worst_wape": item["selection_worst_wape"],
                        "pseudo_wape": item["pseudo_blind"]["wape"],
                        "pseudo_improvement_vs_baseline": item["pseudo_improvement_vs_baseline"],
                    }
                    for item in eligible[:10]
                ],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: prescreen_who_fluid_rates.py OUTPUT_JSON")
    main(sys.argv[1])
