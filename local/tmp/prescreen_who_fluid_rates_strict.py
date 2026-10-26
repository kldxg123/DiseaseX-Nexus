from __future__ import annotations

import json
import statistics
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

import prescreen_who_fluid_rates as source
from fresh_opt_prescreen import Config, _calendarize, _configs, _hash, evaluate_block


def evaluate_strict(
    key: tuple[str, str], by_week: dict[Any, list[float]], window: dict[str, Any], name: str
) -> dict[str, Any]:
    country, metric = key
    track_key = f"{country}:influenza:{metric}:week"
    conflicts = sum(1 for values in by_week.values() if max(values) - min(values) > 1e-12)
    if conflicts:
        return {"track_key": track_key, "status": "EXCLUDED_DUPLICATE_CONFLICT"}
    dates, values = _calendarize(sorted((week, values[0]) for week, values in by_week.items()))
    nonnull = sum(value is not None for value in values)
    if len(values) < 52 * 7 or nonnull / len(values) < 0.80:
        return {"track_key": track_key, "status": "EXCLUDED_PREFIX_COVERAGE"}
    block_ends = [len(values) - 1 - 52 * offset for offset in (5, 4, 3, 2, 1, 0)]
    ranked: list[tuple[float, float, str, Config, list[dict[str, Any]]]] = []
    for config in _configs():
        blocks = [evaluate_block(values, end, config) for end in block_ends[:5]]
        wapes = [block.get("wape") for block in blocks]
        all_horizons = [value for block in blocks for value in block["per_horizon_wape"].values()]
        if (
            any(value is None for value in wapes)
            or any(block["paired_n"] < 40 for block in blocks)
            or any(value is not None and float(value) > 0.45 for value in all_horizons)
        ):
            continue
        numeric = [float(value) for value in wapes if value is not None]
        ranked.append((statistics.median(numeric), max(numeric), _hash(asdict(config)), config, blocks))
    ranked.sort(key=lambda item: (item[0], item[1], item[2]))
    if not ranked:
        return {"track_key": track_key, "status": "NO_STRICT_HORIZON_CONFIG"}
    median_wape, worst_wape, config_sha, config, blocks = ranked[0]
    pseudo = evaluate_block(values, block_ends[5], config)
    baselines: list[tuple[float, str]] = []
    for baseline_id, baseline_config in (
        ("last_value", Config("last_value")),
        ("seasonal_naive", Config("seasonal_mean", years=1)),
    ):
        metrics = evaluate_block(values, block_ends[5], baseline_config)
        if metrics.get("wape") is not None:
            baselines.append((float(metrics["wape"]), baseline_id))
    baselines.sort()
    baseline = baselines[0] if baselines else None
    pseudo_wape = pseudo.get("wape")
    improvement = (
        (baseline[0] - float(pseudo_wape)) / baseline[0]
        if baseline and baseline[0] > 0 and pseudo_wape is not None
        else None
    )
    pseudo_horizon_ok = all(
        value is None or float(value) <= 0.42 for value in pseudo["per_horizon_wape"].values()
    )
    pre_gate = bool(
        median_wape <= 0.25
        and worst_wape <= 0.35
        and pseudo_wape is not None
        and float(pseudo_wape) <= 0.27
        and improvement is not None
        and improvement >= 0.01
        and pseudo["paired_n"] >= 40
        and pseudo_horizon_ok
    )
    return {
        "track_key": track_key,
        "country_name": name,
        "status": "WHO_FLUID_STRICT_PREFIX_PRESCREENED",
        "config": asdict(config),
        "config_sha256": config_sha,
        "selection_median_wape": median_wape,
        "selection_worst_wape": worst_wape,
        "selection_blocks": blocks,
        "pseudo_blind": pseudo,
        "pseudo_best_baseline": None if baseline is None else {"wape": baseline[0], "id": baseline[1]},
        "pseudo_improvement_vs_baseline": improvement,
        "pre_gate": pre_gate,
        "prefix_sha256": _hash([(d.isoformat(), v) for d, v in zip(dates, values, strict=True)]),
        "hidden_start": window["hidden_start"].isoformat(),
        "hidden_end": window["last"].isoformat(),
        "hidden_present_weeks": window["hidden_present"],
        "hidden_values_parsed": False,
    }


def main(output_path: str) -> None:
    windows, names = source.discover_windows()
    prefixes = source.load_prefixes(windows)
    results = [
        evaluate_strict(key, prefixes[key], window, names.get(key[0], key[0]))
        for key, window in sorted(windows.items())
        if key in prefixes
    ]
    eligible = [item for item in results if item.get("pre_gate")]
    eligible.sort(key=lambda item: (float(item["pseudo_blind"]["wape"]), item["track_key"]))
    payload = {
        "source": "WHO_FLUID_OFFICIAL_FROZEN",
        "source_sha256": source.RAW.name,
        "hidden_values_parsed": False,
        "candidate_count": len(results),
        "pre_gate_count": len(eligible),
        "selected_track_keys": [item["track_key"] for item in eligible],
        "results": results,
    }
    Path(output_path).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "output": output_path,
                "sha256": _hash(payload),
                "pre_gate_count": len(eligible),
                "selected_track_keys": payload["selected_track_keys"],
                "eligible": [
                    {
                        "track_key": item["track_key"],
                        "config": item["config"],
                        "selection_median_wape": item["selection_median_wape"],
                        "selection_worst_wape": item["selection_worst_wape"],
                        "pseudo_wape": item["pseudo_blind"]["wape"],
                        "pseudo_improvement_vs_baseline": item["pseudo_improvement_vs_baseline"],
                    }
                    for item in eligible
                ],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: prescreen_who_fluid_rates_strict.py OUTPUT_JSON")
    main(sys.argv[1])
