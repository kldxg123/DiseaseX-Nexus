from __future__ import annotations

import hashlib
import json
import math
import statistics
import sys
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable

from epidemic_core.kg_r3c.dbutil import connect


DISEASE_ID = "6c9e2d3e-3a33-525a-8fbf-2b2eb1db4982"
METRIC = "influenza_positive_detections"
HIDDEN_WEEKS = 52
CANDIDATES = (
    ("COL", "CO"),
    ("MEX", "MX"),
    ("AUS", "AU"),
    ("THA", "TH"),
    ("NOR", "NO"),
    ("BRA", "BR"),
    ("MDG", "MG"),
    ("ARG", "AR"),
)


@dataclass(frozen=True)
class Config:
    mode: str
    years: int = 1
    recent_window: int = 8
    alpha: float = 1.0
    ratio_low: float = 0.25
    ratio_high: float = 4.0


def _hash(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(raw).hexdigest()


def _mean(values: Iterable[float | None]) -> float | None:
    clean = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    return sum(clean) / len(clean) if clean else None


def _get(values: list[float | None], index: int) -> float | None:
    return values[index] if 0 <= index < len(values) else None


def _seasonal(values: list[float | None], origin: int, horizon: int, years: int) -> float | None:
    target = origin + horizon
    weighted: list[tuple[float, float]] = []
    for year in range(1, years + 1):
        value = _get(values, target - 52 * year)
        if value is not None:
            weighted.append((value, 1.0 / year))
    if not weighted:
        return None
    return sum(value * weight for value, weight in weighted) / sum(weight for _, weight in weighted)


def _recent(values: list[float | None], origin: int, window: int, shift: int = 0) -> float | None:
    end = origin + 1 - shift
    start = end - window
    if start < 0:
        return None
    segment = values[start:end]
    if len(segment) != window or any(v is None for v in segment):
        return None
    return _mean(segment)


def predict(values: list[float | None], origin: int, horizon: int, config: Config) -> float | None:
    seasonal = _seasonal(values, origin, horizon, config.years)
    recent = _recent(values, origin, config.recent_window)
    if config.mode == "last_value":
        return _get(values, origin)
    if config.mode == "rolling_mean":
        return recent
    if seasonal is None:
        return None
    if config.mode == "seasonal_mean":
        return max(seasonal, 0.0)

    previous_level = _recent(values, origin, config.recent_window, shift=52)
    scaled = seasonal
    delta = seasonal
    if recent is not None and previous_level is not None:
        if previous_level > 0:
            ratio = min(max(recent / previous_level, config.ratio_low), config.ratio_high)
            scaled = seasonal * ratio
        delta = max(seasonal + recent - previous_level, 0.0)
    if config.mode == "seasonal_scaled":
        return max(scaled, 0.0)
    if config.mode == "seasonal_delta":
        return max(delta, 0.0)
    if config.mode == "seasonal_blend" and recent is not None:
        return max(config.alpha * scaled + (1.0 - config.alpha) * recent, 0.0)
    if config.mode == "seasonal_delta_blend" and recent is not None:
        return max(config.alpha * delta + (1.0 - config.alpha) * recent, 0.0)
    return max(seasonal, 0.0)


def _configs() -> list[Config]:
    configs = [Config("last_value"), Config("seasonal_mean", years=1)]
    configs.extend(Config("rolling_mean", recent_window=w) for w in (4, 8, 13, 26))
    for years in (1, 2, 3):
        configs.append(Config("seasonal_mean", years=years))
        for window in (4, 8, 13, 26):
            for low, high in ((0.5, 2.0), (0.25, 4.0)):
                configs.append(Config("seasonal_scaled", years, window, 1.0, low, high))
                configs.append(Config("seasonal_delta", years, window, 1.0, low, high))
                for alpha in (0.4, 0.6, 0.8):
                    configs.append(Config("seasonal_blend", years, window, alpha, low, high))
                    configs.append(Config("seasonal_delta_blend", years, window, alpha, low, high))
    unique: dict[str, Config] = {}
    for config in configs:
        unique[_hash(asdict(config))] = config
    return list(unique.values())


def _calendarize(rows: list[tuple[date, float | None]]) -> tuple[list[date], list[float | None]]:
    by_date = {period: value for period, value in rows}
    first = min(by_date)
    last = max(by_date)
    dates: list[date] = []
    values: list[float | None] = []
    current = first
    while current <= last:
        dates.append(current)
        values.append(by_date.get(current))
        current += timedelta(days=7)
    return dates, values


def _wape(actual: list[float], predicted: list[float]) -> float | None:
    denominator = sum(abs(v) for v in actual)
    if not actual or denominator <= 0:
        return None
    return sum(abs(a - p) for a, p in zip(actual, predicted, strict=True)) / denominator


def evaluate_block(
    values: list[float | None], block_end: int, config: Config
) -> dict[str, Any]:
    actual: list[float] = []
    predicted: list[float] = []
    per_horizon: dict[int, tuple[list[float], list[float]]] = {
        h: ([], []) for h in (1, 2, 3, 4)
    }
    first_target = block_end - 51
    for target_start in range(first_target, block_end + 1, 4):
        origin = target_start - 1
        for horizon in (1, 2, 3, 4):
            target = origin + horizon
            if target > block_end:
                continue
            y = _get(values, target)
            yhat = predict(values, origin, horizon, config)
            if y is None or yhat is None:
                continue
            actual.append(float(y))
            predicted.append(float(yhat))
            per_horizon[horizon][0].append(float(y))
            per_horizon[horizon][1].append(float(yhat))
    wape = _wape(actual, predicted)
    return {
        "wape": wape,
        "agreement_pct": None if wape is None else max(0.0, 1.0 - wape) * 100.0,
        "paired_n": len(actual),
        "per_horizon_wape": {
            str(h): _wape(pair[0], pair[1]) for h, pair in per_horizon.items()
        },
    }


def _load_tuning_rows(cur: Any, country_alpha2: str) -> tuple[list[tuple[date, float]], dict[str, Any]]:
    params = (country_alpha2, DISEASE_ID, METRIC, "week")
    cur.execute(
        """
        with chosen as (
          select distinct on (period_start)
                 period_start, value_numeric
          from epidemic_p1_12.trusted_dashboard_series_v1
          where country_alpha2 = %s and disease_id = %s
            and metric_code = %s and granularity = %s
          order by period_start, revision desc nulls last, as_of desc, observation_id desc
        ), ranked as (
          select period_start, value_numeric,
                 row_number() over (order by period_start desc) as reverse_rank
          from chosen
        )
        select period_start, value_numeric
        from ranked
        where reverse_rank > 52
        order by period_start
        """,
        params,
    )
    rows = [(row[0], float(row[1])) for row in cur.fetchall() if row[1] is not None]
    cur.execute(
        """
        with chosen as (
          select distinct period_start
          from epidemic_p1_12.trusted_dashboard_series_v1
          where country_alpha2 = %s and disease_id = %s
            and metric_code = %s and granularity = %s
        ), hidden as (
          select period_start, row_number() over (order by period_start desc) as reverse_rank
          from chosen
        )
        select count(*), min(period_start), max(period_start)
        from hidden where reverse_rank <= 52
        """,
        params,
    )
    hidden = cur.fetchone()
    return rows, {
        "hidden_count": int(hidden[0]),
        "hidden_min_period": hidden[1].isoformat(),
        "hidden_max_period": hidden[2].isoformat(),
        "hidden_values_read": False,
    }


def main() -> None:
    configs = _configs()
    results: list[dict[str, Any]] = []
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            cur.execute("SET LOCAL statement_timeout = '120s'")
            cur.execute("SET LOCAL idle_in_transaction_session_timeout = '180s'")
            for country_iso3, country_alpha2 in CANDIDATES:
                track_key = f"{country_iso3}:influenza:{METRIC}:week"
                cur.execute(
                    "select count(*) from epidemic_kg.kg_qwen_heldout_registry where track_key = %s",
                    (track_key,),
                )
                if int(cur.fetchone()[0]) != 0:
                    results.append({"track_key": track_key, "status": "EXCLUDED_ALREADY_REGISTERED"})
                    continue
                rows, hidden_meta = _load_tuning_rows(cur, country_alpha2)
                dates, values = _calendarize(rows)
                nonnull = sum(v is not None for v in values)
                if len(values) < 52 * 5 or nonnull / len(values) < 0.75:
                    results.append(
                        {
                            "track_key": track_key,
                            "status": "EXCLUDED_INSUFFICIENT_CALENDAR_COVERAGE",
                            "calendar_weeks_tuning": len(values),
                            "nonnull_tuning": nonnull,
                            **hidden_meta,
                        }
                    )
                    continue
                block_ends = [len(values) - 1 - 52 * offset for offset in (3, 2, 1, 0)]
                scored: list[tuple[float, float, Config, list[dict[str, Any]]]] = []
                for config in configs:
                    blocks = [evaluate_block(values, end, config) for end in block_ends]
                    wapes = [float(block["wape"]) for block in blocks if block["wape"] is not None]
                    if len(wapes) != 4 or any(block["paired_n"] < 40 for block in blocks):
                        continue
                    scored.append((statistics.median(wapes), max(wapes), config, blocks))
                scored.sort(key=lambda item: (item[0], item[1], _hash(asdict(item[2]))))
                best = scored[0] if scored else None
                baseline_configs = [Config("last_value"), Config("seasonal_mean", years=1)]
                baseline = []
                for config in baseline_configs:
                    blocks = [evaluate_block(values, end, config) for end in block_ends]
                    wapes = [float(block["wape"]) for block in blocks if block["wape"] is not None]
                    if len(wapes) == 4:
                        baseline.append((statistics.median(wapes), config, blocks))
                baseline.sort(key=lambda item: item[0])
                payload: dict[str, Any] = {
                    "track_key": track_key,
                    "status": "PRESCREENED_TUNING_ONLY",
                    "calendar_weeks_tuning": len(values),
                    "nonnull_tuning": nonnull,
                    "tuning_min_period": dates[0].isoformat(),
                    "tuning_max_period": dates[-1].isoformat(),
                    "tuning_series_sha256": _hash(
                        [(d.isoformat(), v) for d, v in zip(dates, values, strict=True)]
                    ),
                    **hidden_meta,
                }
                if best is not None:
                    median_wape, worst_wape, config, blocks = best
                    latest_wape = blocks[-1]["wape"]
                    baseline_wape = baseline[0][0] if baseline else None
                    improvement = (
                        None
                        if baseline_wape is None or baseline_wape <= 0
                        else (baseline_wape - median_wape) / baseline_wape
                    )
                    payload["best"] = {
                        "config": asdict(config),
                        "config_sha256": _hash(asdict(config)),
                        "median_wape": median_wape,
                        "worst_wape": worst_wape,
                        "latest_pseudo_wape": latest_wape,
                        "median_agreement_pct": max(0.0, 1.0 - median_wape) * 100.0,
                        "improvement_vs_best_baseline": improvement,
                        "blocks": blocks,
                    }
                    payload["best_baseline"] = None if not baseline else {
                        "config": asdict(baseline[0][1]),
                        "median_wape": baseline[0][0],
                        "blocks": baseline[0][2],
                    }
                    payload["pre_gate"] = bool(
                        median_wape <= 0.28
                        and worst_wape <= 0.35
                        and latest_wape is not None
                        and float(latest_wape) <= 0.30
                        and improvement is not None
                        and improvement >= 0.01
                        and all(
                            (w is None or float(w) <= 0.45)
                            for block in blocks
                            for w in block["per_horizon_wape"].values()
                        )
                    )
                else:
                    payload["pre_gate"] = False
                    payload["reason"] = "NO_CONFIG_WITH_4X40_PAIRED_ROWS"
                results.append(payload)
            cur.execute("show transaction_read_only")
            read_only = cur.fetchone()[0]
            conn.rollback()

    eligible = [item for item in results if item.get("pre_gate")]
    eligible.sort(key=lambda item: (item["best"]["median_wape"], item["track_key"]))
    output = {
        "transaction_read_only": read_only,
        "hidden_values_read": False,
        "hidden_weeks_reserved_per_candidate": HIDDEN_WEEKS,
        "candidate_count": len(results),
        "pre_gate_count": len(eligible),
        "selected_track_key": eligible[0]["track_key"] if eligible else None,
        "selection_rule": "median<=0.28,worst<=0.35,latest<=0.30,baseline_improvement>=1%,all_horizons<=0.45",
        "results": results,
    }
    rendered = json.dumps(output, ensure_ascii=False, indent=2)
    if len(sys.argv) == 2:
        Path(sys.argv[1]).write_text(rendered + "\n", encoding="utf-8")
        print(json.dumps({"output": sys.argv[1], "sha256": _hash(output), "pre_gate_count": len(eligible), "selected_track_key": output["selected_track_key"]}))
    else:
        print(rendered)


if __name__ == "__main__":
    main()
