from __future__ import annotations

import csv
import hashlib
import json
import math
import statistics
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

RAW = Path("USER_HOME/projects/openclaw-fresh-ukhsa-20260814/runtime/fresh_source/who_fluid_frozen_20260814.csv")
RAW_SHA = "99fe45c41db35bcede7153308c4a4a3ed43369909709618bbe9bd34af567e165"


@dataclass(frozen=True)
class Config:
    mode: str
    years: int = 1
    recent_window: int = 8
    alpha: float = 1.0


def mean(values: Iterable[float | None]) -> float | None:
    clean = [float(value) for value in values if value is not None]
    return sum(clean) / len(clean) if clean else None


def get(values: list[float | None], index: int) -> float | None:
    return values[index] if 0 <= index < len(values) else None


def recent(values: list[float | None], origin: int, window: int, shift: int = 0) -> float | None:
    end = origin + 1 - shift
    start = end - window
    segment = values[start:end] if start >= 0 else []
    return None if len(segment) != window or any(value is None for value in segment) else mean(segment)


def predict(values: list[float | None], origin: int, horizon: int, config: Config) -> float | None:
    if config.mode == "last_value":
        return get(values, origin)
    if config.mode == "rolling_mean":
        return recent(values, origin, config.recent_window)
    target = origin + horizon
    refs = [
        (get(values, target - 52 * year), 1.0 / year)
        for year in range(1, config.years + 1)
    ]
    refs = [(value, weight) for value, weight in refs if value is not None]
    if not refs:
        return None
    seasonal = sum(float(value) * weight for value, weight in refs) / sum(weight for _, weight in refs)
    now = recent(values, origin, config.recent_window)
    old = recent(values, origin, config.recent_window, 52)
    if config.mode == "seasonal_mean":
        return max(seasonal, 0.0)
    if now is None or old is None:
        return None
    delta = max(seasonal + now - old, 0.0)
    if config.mode == "seasonal_delta":
        return delta
    if config.mode == "seasonal_delta_blend":
        return max(config.alpha * delta + (1.0 - config.alpha) * now, 0.0)
    return None


def wape(actual: list[float], predicted: list[float]) -> float | None:
    denominator = sum(abs(value) for value in actual)
    return None if not actual or denominator <= 0 else sum(abs(a - p) for a, p in zip(actual, predicted)) / denominator


def cells(values: list[float | None], block_end: int, config: Config) -> list[tuple[int, float, float]]:
    out = []
    for target_start in range(block_end - 51, block_end + 1, 4):
        origin = target_start - 1
        for horizon in (1, 2, 3, 4):
            target = origin + horizon
            if target > block_end:
                continue
            actual = get(values, target)
            yhat = predict(values, origin, horizon, config)
            if actual is not None and yhat is not None:
                out.append((horizon, float(actual), float(yhat)))
    return out


def metrics(blended: list[tuple[int, float, float]]) -> dict:
    overall = wape([item[1] for item in blended], [item[2] for item in blended])
    return {
        "wape": overall,
        "paired_n": len(blended),
        "per_horizon_wape": {
            str(h): wape(
                [item[1] for item in blended if item[0] == h],
                [item[2] for item in blended if item[0] == h],
            )
            for h in (1, 2, 3, 4)
        },
    }


def main() -> None:
    if hashlib.sha256(RAW.read_bytes()).hexdigest() != RAW_SHA:
        raise SystemExit("RAW_SHA_MISMATCH")
    dates: set[date] = set()
    with RAW.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("COUNTRY_CODE") == "KHM" and (row.get("AGEGROUP_CODE") or "").casefold() == "all":
                dates.add(datetime.strptime(row["ISO_WEEKSTARTDATE"][:10], "%Y-%m-%d").date())
    ordered_dates = sorted(dates)
    hidden_start = ordered_dates[-52]
    by_date: dict[date, float] = {}
    with RAW.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("COUNTRY_CODE") != "KHM" or (row.get("AGEGROUP_CODE") or "").casefold() != "all":
                continue
            current = datetime.strptime(row["ISO_WEEKSTARTDATE"][:10], "%Y-%m-%d").date()
            if current >= hidden_start:
                continue
            try:
                cases = float(row["ILI_CASE"])
                outpatients = float(row["ILI_OUTPATIENTS"])
            except (TypeError, ValueError):
                continue
            if cases < 0 or outpatients <= 0:
                continue
            value = 1000.0 * cases / outpatients
            if current in by_date and abs(by_date[current] - value) > 1e-12:
                raise SystemExit("DUPLICATE_CONFLICT")
            by_date[current] = value
    first, last = min(by_date), max(by_date)
    values: list[float | None] = []
    current = first
    while current <= last:
        values.append(by_date.get(current))
        current += timedelta(weeks=1)
    block_ends = [len(values) - 1 - 52 * offset for offset in (5, 4, 3, 2, 1, 0)]
    configs = [Config("seasonal_mean", years=years) for years in (1, 2, 3)]
    configs += [
        Config(mode, years, window, alpha)
        for mode in ("seasonal_delta", "seasonal_delta_blend")
        for years in (1, 2, 3)
        for window in (4, 8, 13, 26)
        for alpha in ((1.0,) if mode == "seasonal_delta" else (0.2, 0.4, 0.6, 0.8))
    ]
    baselines = [Config("last_value"), *[Config("rolling_mean", recent_window=w) for w in (4, 8, 13, 26)]]
    selection_ends = block_ends[:5]
    candidates = []
    for config in configs:
        for baseline in baselines:
            weights: dict[int, float] = {}
            for horizon in (1, 2, 3, 4):
                best = None
                for weight in (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0):
                    merged = []
                    for end in selection_ends:
                        model = {(h, a): p for h, a, p in cells(values, end, config) if h == horizon}
                        base = {(h, a): p for h, a, p in cells(values, end, baseline) if h == horizon}
                        for key in model.keys() & base.keys():
                            merged.append((horizon, key[1], weight * model[key] + (1.0 - weight) * base[key]))
                    score = wape([item[1] for item in merged], [item[2] for item in merged])
                    marker = (float(score) if score is not None else math.inf, weight)
                    if best is None or marker < best:
                        best = marker
                weights[horizon] = best[1]
            blocks = []
            for end in selection_ends:
                model_cells = cells(values, end, config)
                base_cells = cells(values, end, baseline)
                base_map = {(h, a): p for h, a, p in base_cells}
                blended = [
                    (h, actual, weights[h] * pred + (1.0 - weights[h]) * base_map[(h, actual)])
                    for h, actual, pred in model_cells
                    if (h, actual) in base_map
                ]
                blocks.append(metrics(blended))
            numeric = [float(block["wape"]) for block in blocks if block["wape"] is not None]
            if len(numeric) != 5:
                continue
            pseudo_model = cells(values, block_ends[5], config)
            pseudo_base = {(h, a): p for h, a, p in cells(values, block_ends[5], baseline)}
            pseudo_cells = [
                (h, actual, weights[h] * pred + (1.0 - weights[h]) * pseudo_base[(h, actual)])
                for h, actual, pred in pseudo_model
                if (h, actual) in pseudo_base
            ]
            pseudo = metrics(pseudo_cells)
            base_metric = metrics(cells(values, block_ends[5], baseline))
            improvement = (
                (float(base_metric["wape"]) - float(pseudo["wape"])) / float(base_metric["wape"])
                if base_metric["wape"] and pseudo["wape"] is not None
                else None
            )
            horizon_values = [value for block in blocks for value in block["per_horizon_wape"].values()]
            candidates.append(
                {
                    "config": asdict(config),
                    "baseline": asdict(baseline),
                    "weights": weights,
                    "selection_median_wape": statistics.median(numeric),
                    "selection_worst_wape": max(numeric),
                    "selection_worst_horizon_wape": max(float(value) for value in horizon_values if value is not None),
                    "pseudo": pseudo,
                    "pseudo_baseline_wape": base_metric["wape"],
                    "pseudo_improvement": improvement,
                }
            )
    candidates.sort(
        key=lambda item: (
            item["selection_worst_horizon_wape"],
            item["selection_worst_wape"],
            item["selection_median_wape"],
        )
    )
    eligible = [
        item
        for item in candidates
        if item["selection_median_wape"] <= 0.22
        and item["selection_worst_wape"] <= 0.32
        and item["selection_worst_horizon_wape"] <= 0.42
        and item["pseudo"]["wape"] is not None
        and item["pseudo"]["wape"] <= 0.24
        and item["pseudo_improvement"] is not None
        and item["pseudo_improvement"] >= 0.05
    ]
    result = {
        "track_key": "KHM:influenza:who_fluid_ili_rate_per_1000_outpatients:week",
        "hidden_start": hidden_start.isoformat(),
        "hidden_count": 52,
        "hidden_values_read": False,
        "candidate_count": len(candidates),
        "eligible_count": len(eligible),
        "best": eligible[0] if eligible else None,
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
