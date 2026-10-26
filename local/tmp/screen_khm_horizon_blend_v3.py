from __future__ import annotations

import csv
import json
import math
import statistics
from dataclasses import asdict
from datetime import datetime, timedelta

import screen_khm_horizon_blend as s


def keyed_cells(values, block_end, config):
    out = {}
    for target_start in range(block_end - 51, block_end + 1, 4):
        origin = target_start - 1
        for horizon in (1, 2, 3, 4):
            target = origin + horizon
            if target > block_end:
                continue
            actual = s.get(values, target)
            predicted = s.predict(values, origin, horizon, config)
            if actual is not None and predicted is not None:
                out[(origin, horizon, target)] = (float(actual), float(predicted))
    return out


def blend(model, baseline, weights):
    cells = []
    for key in sorted(model.keys() & baseline.keys()):
        _, horizon, _ = key
        actual, prediction = model[key]
        baseline_prediction = baseline[key][1]
        weight = weights[horizon]
        cells.append((horizon, actual, weight * prediction + (1.0 - weight) * baseline_prediction))
    return cells


def main() -> None:
    dates = set()
    with s.RAW.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("COUNTRY_CODE") == "KHM" and (row.get("AGEGROUP_CODE") or "").casefold() == "all":
                dates.add(datetime.strptime(row["ISO_WEEKSTARTDATE"][:10], "%Y-%m-%d").date())
    hidden_start = sorted(dates)[-52]
    by_date = {}
    with s.RAW.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("COUNTRY_CODE") != "KHM" or (row.get("AGEGROUP_CODE") or "").casefold() != "all":
                continue
            current = datetime.strptime(row["ISO_WEEKSTARTDATE"][:10], "%Y-%m-%d").date()
            if current >= hidden_start:
                continue
            try:
                cases, denominator = float(row["ILI_CASE"]), float(row["ILI_OUTPATIENTS"])
            except (TypeError, ValueError):
                continue
            if cases >= 0 and denominator > 0:
                value = 1000 * cases / denominator
                if current in by_date and abs(by_date[current] - value) > 1e-12:
                    raise SystemExit("DUPLICATE_CONFLICT")
                by_date[current] = value
    first, last = min(by_date), max(by_date)
    values = []
    current = first
    while current <= last:
        values.append(by_date.get(current))
        current += timedelta(weeks=1)
    ends = [len(values) - 1 - 52 * offset for offset in (5, 4, 3, 2, 1, 0)]
    configs = [s.Config("seasonal_mean", years=y) for y in (1, 2, 3)]
    configs += [
        s.Config(mode, y, window, alpha)
        for mode in ("seasonal_delta", "seasonal_delta_blend")
        for y in (1, 2, 3)
        for window in (4, 8, 13, 26)
        for alpha in ((1.0,) if mode == "seasonal_delta" else (0.2, 0.4, 0.6, 0.8))
    ]
    baselines = [s.Config("last_value"), *[s.Config("rolling_mean", recent_window=w) for w in (4, 8, 13, 26)]]
    results = []
    for config in configs:
        for baseline in baselines:
            weights = {}
            for horizon in (1, 2, 3, 4):
                scored = []
                for weight in (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0):
                    combined = []
                    for end in ends[:5]:
                        model = {key: value for key, value in keyed_cells(values, end, config).items() if key[1] == horizon}
                        base = {key: value for key, value in keyed_cells(values, end, baseline).items() if key[1] == horizon}
                        combined.extend(blend(model, base, {horizon: weight}))
                    score = s.wape([item[1] for item in combined], [item[2] for item in combined])
                    scored.append((math.inf if score is None else score, weight))
                weights[horizon] = min(scored)[1]
            blocks = []
            for end in ends[:5]:
                blocks.append(s.metrics(blend(keyed_cells(values, end, config), keyed_cells(values, end, baseline), weights)))
            if any(block["wape"] is None or block["paired_n"] < 40 for block in blocks):
                continue
            pseudo_model = keyed_cells(values, ends[5], config)
            pseudo_baseline = keyed_cells(values, ends[5], baseline)
            pseudo = s.metrics(blend(pseudo_model, pseudo_baseline, weights))
            base_cells = [(key[1], value[0], value[1]) for key, value in sorted(pseudo_baseline.items())]
            base_metrics = s.metrics(base_cells)
            if pseudo["wape"] is None or base_metrics["wape"] is None or base_metrics["wape"] <= 0:
                continue
            numeric = [float(block["wape"]) for block in blocks]
            horizon_values = [float(value) for block in blocks for value in block["per_horizon_wape"].values() if value is not None]
            results.append({
                "config": asdict(config),
                "baseline": asdict(baseline),
                "weights": weights,
                "selection_median_wape": statistics.median(numeric),
                "selection_worst_wape": max(numeric),
                "selection_worst_horizon_wape": max(horizon_values),
                "pseudo": pseudo,
                "pseudo_baseline_wape": base_metrics["wape"],
                "pseudo_improvement": (base_metrics["wape"] - pseudo["wape"]) / base_metrics["wape"],
            })
    eligible = [item for item in results if item["selection_median_wape"] <= 0.22
                and item["selection_worst_wape"] <= 0.32
                and item["selection_worst_horizon_wape"] <= 0.42
                and item["pseudo"]["wape"] <= 0.24 and item["pseudo_improvement"] >= 0.05]
    eligible.sort(key=lambda item: (item["selection_worst_horizon_wape"], item["selection_worst_wape"], item["pseudo"]["wape"]))
    print(json.dumps({"track_key": "KHM:influenza:who_fluid_ili_rate_per_1000_outpatients:week",
                      "hidden_start": hidden_start.isoformat(), "hidden_count": 52,
                      "hidden_values_read": False, "candidate_count": len(results),
                      "eligible_count": len(eligible), "best": eligible[0] if eligible else None}, sort_keys=True))


if __name__ == "__main__":
    main()
