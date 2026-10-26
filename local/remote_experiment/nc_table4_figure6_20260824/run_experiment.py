from __future__ import annotations

import csv
import hashlib
import itertools
import json
import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable

import numpy as np


ROOT = Path(__file__).resolve().parent
INPUTS = ROOT / "inputs"
OUTPUTS = ROOT / "outputs"
OUTPUTS.mkdir(parents=True, exist_ok=True)
SEED = 20260824
HORIZONS = (1, 2, 3, 4)


@dataclass(frozen=True)
class SeriesSpec:
    series_id: str
    country: str
    disease: str
    metric: str
    cadence: str
    season: int
    test_periods: int
    min_train: int
    expected_rows: int
    filename: str


SPECS = (
    SeriesSpec("AUT_legionella_month", "AUT", "legionella", "legionnaires_reported_cases", "month", 12, 36, 120, 240, "aut_legionella_month.csv"),
    SeriesSpec("FIN_hantavirus_week", "FIN", "hantavirus", "puumala_virus_reports", "week", 52, 52, 160, 445, "fin_hantavirus_week.csv"),
    SeriesSpec("BRA_dengue_week", "BRA", "dengue", "cases_new", "week", 52, 16, 52, 74, "bra_dengue_week.csv"),
)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_series(spec: SeriesSpec) -> tuple[list[date], np.ndarray, dict[str, object]]:
    path = INPUTS / spec.filename
    rows = list(csv.DictReader(path.open("r", encoding="utf-8-sig", newline="")))
    dates = [date.fromisoformat(r["period_start"]) for r in rows]
    values = np.asarray([float(r["value_numeric"]) for r in rows], dtype=float)
    unique_dates = len(set(dates)) == len(dates)
    sorted_dates = dates == sorted(dates)
    nonnegative = bool(np.all(np.isfinite(values)) and np.all(values >= 0))
    gaps = 0
    for a, b in zip(dates, dates[1:]):
        if spec.cadence == "week":
            gaps += int((b - a).days != 7)
        else:
            expected_month = a.month % 12 + 1
            expected_year = a.year + (1 if a.month == 12 else 0)
            gaps += int((b.year, b.month) != (expected_year, expected_month))
    meta = {
        "path": str(path.relative_to(ROOT)),
        "sha256": sha256(path),
        "row_count": len(rows),
        "expected_rows": spec.expected_rows,
        "first_date": dates[0].isoformat(),
        "last_date": dates[-1].isoformat(),
        "unique_dates": unique_dates,
        "sorted_dates": sorted_dates,
        "nonnegative_finite": nonnegative,
        "cadence_gap_count": gaps,
    }
    return dates, values, meta


def last_value(y: np.ndarray, horizon: int, season: int) -> np.ndarray:
    return np.repeat(y[-1], horizon).astype(float)


def rolling_mean(y: np.ndarray, horizon: int, season: int, window: int) -> np.ndarray:
    return np.repeat(float(np.mean(y[-min(window, len(y)) :])), horizon)


def seasonal_naive(y: np.ndarray, horizon: int, season: int) -> np.ndarray:
    if len(y) < season:
        return last_value(y, horizon, season)
    out = []
    extended = list(map(float, y))
    for _ in range(horizon):
        pred = extended[-season]
        extended.append(pred)
        out.append(pred)
    return np.asarray(out)


def drift(y: np.ndarray, horizon: int, season: int, window: int = 13) -> np.ndarray:
    use = y[-min(window, len(y)) :]
    x = np.arange(len(use), dtype=float)
    if len(use) < 2 or np.allclose(use, use[0]):
        slope = 0.0
    else:
        slope = float(np.polyfit(x, use, 1)[0])
    return np.maximum(0.0, y[-1] + slope * np.arange(1, horizon + 1))


def holt(y: np.ndarray, horizon: int, season: int, alpha: float, beta: float) -> np.ndarray:
    level = float(y[0])
    trend = float(y[1] - y[0]) if len(y) > 1 else 0.0
    for value in y[1:]:
        old_level = level
        level = alpha * float(value) + (1 - alpha) * (level + trend)
        trend = beta * (level - old_level) + (1 - beta) * trend
    return np.maximum(0.0, level + trend * np.arange(1, horizon + 1))


def ridge_recursive(y: np.ndarray, horizon: int, season: int, lag_count: int, alpha: float, include_season: bool) -> np.ndarray:
    lags = list(range(1, lag_count + 1))
    if include_season and season not in lags and len(y) >= season + 16:
        lags.append(season)
    max_lag = max(lags)
    if len(y) <= max_lag + 8:
        return rolling_mean(y, horizon, season, 4)

    def features(history: list[float], idx: int) -> list[float]:
        phase = 2.0 * math.pi * idx / season
        return [1.0, idx / max(1.0, len(y)), math.sin(phase), math.cos(phase)] + [history[-lag] for lag in lags]

    X: list[list[float]] = []
    target: list[float] = []
    history = list(map(float, y))
    for idx in range(max_lag, len(y)):
        hist = history[:idx]
        X.append(features(hist, idx))
        target.append(history[idx])
    xa = np.asarray(X, dtype=float)
    ya = np.asarray(target, dtype=float)
    scale = np.maximum(np.std(xa, axis=0), 1e-8)
    scale[0] = 1.0
    xs = xa / scale
    reg = np.eye(xs.shape[1]) * alpha
    reg[0, 0] = 0.0
    coef = np.linalg.solve(xs.T @ xs + reg, xs.T @ ya)
    extended = history[:]
    out: list[float] = []
    for step in range(horizon):
        idx = len(extended)
        xf = np.asarray(features(extended, idx), dtype=float) / scale
        pred = max(0.0, float(xf @ coef))
        extended.append(pred)
        out.append(pred)
    return np.asarray(out)


Predictor = Callable[[np.ndarray, int, int], np.ndarray]


def candidate_library(spec: SeriesSpec, profile_aware: bool) -> dict[str, Predictor]:
    base: dict[str, Predictor] = {
        "last_value": last_value,
        "rolling_mean_4": lambda y, h, s: rolling_mean(y, h, s, 4),
        "rolling_mean_8": lambda y, h, s: rolling_mean(y, h, s, 8),
        "drift_13": lambda y, h, s: drift(y, h, s, 13),
        "holt_0.3_0.05": lambda y, h, s: holt(y, h, s, 0.3, 0.05),
        "holt_0.6_0.10": lambda y, h, s: holt(y, h, s, 0.6, 0.10),
        "ridge_l4_a1": lambda y, h, s: ridge_recursive(y, h, s, 4, 1.0, False),
        "ridge_l8_a10": lambda y, h, s: ridge_recursive(y, h, s, 8, 10.0, False),
    }
    if profile_aware:
        base["seasonal_naive"] = seasonal_naive
        base["ridge_seasonal_a10"] = lambda y, h, s: ridge_recursive(y, h, s, 4, 10.0, True)
        if spec.cadence == "month":
            base["rolling_mean_12"] = lambda y, h, s: rolling_mean(y, h, s, 12)
        else:
            base["rolling_mean_13"] = lambda y, h, s: rolling_mean(y, h, s, 13)
    return base


def safe_predict(fn: Predictor, y: np.ndarray, horizon: int, season: int) -> np.ndarray:
    try:
        pred = np.asarray(fn(y, horizon, season), dtype=float)
    except (ValueError, np.linalg.LinAlgError, FloatingPointError):
        pred = last_value(y, horizon, season)
    if pred.shape != (horizon,) or not np.all(np.isfinite(pred)):
        pred = last_value(y, horizon, season)
    return np.maximum(0.0, pred)


def wape(actual: np.ndarray, pred: np.ndarray) -> float:
    denom = float(np.sum(np.abs(actual)))
    return float(np.sum(np.abs(actual - pred)) / denom) if denom > 0 else float("nan")


def metric_dict(actual: np.ndarray, pred: np.ndarray) -> dict[str, float | int]:
    err = pred - actual
    value = wape(actual, pred)
    return {
        "n": int(len(actual)),
        "mae": float(np.mean(np.abs(err))),
        "rmse": float(np.sqrt(np.mean(err**2))),
        "wape": value,
        "agreement_pct": max(0.0, 1.0 - value) * 100.0 if math.isfinite(value) else float("nan"),
    }


def validation_panel(y: np.ndarray, t: int, spec: SeriesSpec, library: dict[str, Predictor]) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[str]]:
    validation_periods = min(36, max(12, t // 5))
    start = max(spec.min_train, t - validation_periods)
    names = sorted(library)
    actual: list[float] = []
    horizons: list[int] = []
    matrix: list[list[float]] = []
    for origin in range(start, t):
        max_h = min(max(HORIZONS), t - origin)
        if max_h < 1:
            continue
        preds = {name: safe_predict(fn, y[:origin], max_h, spec.season) for name, fn in library.items()}
        for h in range(1, max_h + 1):
            actual.append(float(y[origin + h - 1]))
            horizons.append(h)
            matrix.append([float(preds[name][h - 1]) for name in names])
    return np.asarray(actual), np.asarray(horizons), np.asarray(matrix), names


def simplex_weights(pred: np.ndarray, actual: np.ndarray, indexes: list[int]) -> np.ndarray:
    if len(indexes) == 1:
        return np.asarray([1.0])
    best = None
    if len(indexes) == 2:
        grids = [(a / 10, 1 - a / 10) for a in range(11)]
    else:
        grids = [(a / 10, b / 10, (10 - a - b) / 10) for a in range(11) for b in range(11 - a)]
    for weights in grids:
        estimate = pred[:, indexes] @ np.asarray(weights)
        score = float(np.sqrt(np.mean((estimate - actual) ** 2)))
        key = (score, tuple(weights))
        if best is None or key < best[0]:
            best = (key, np.asarray(weights, dtype=float))
    assert best is not None
    return best[1]


def choose_config(y: np.ndarray, t: int, spec: SeriesSpec, profile_aware: bool) -> dict[str, object]:
    library = candidate_library(spec, profile_aware)
    actual, horizons, panel, names = validation_panel(y, t, spec, library)
    scores = []
    for idx, name in enumerate(names):
        scores.append((wape(actual, panel[:, idx]), name, idx))
    scores.sort(key=lambda x: (x[0], x[1]))
    top = scores[:3]
    indexes = [x[2] for x in top]
    weights = simplex_weights(panel, actual, indexes)
    full_pred = panel[:, indexes] @ weights
    residuals = {h: [] for h in HORIZONS}
    for h, a, p in zip(horizons, actual, full_pred):
        residuals[int(h)].append(float(a - p))
    return {
        "library": library,
        "top_names": [x[1] for x in top],
        "weights": weights,
        "validation_wape": wape(actual, full_pred),
        "residuals": residuals,
        "all_scores": {name: float(score) for score, name, _ in scores},
    }


def apply_config(config: dict[str, object], y_train: np.ndarray, spec: SeriesSpec) -> np.ndarray:
    library = config["library"]
    names = config["top_names"]
    weights = config["weights"]
    panel = np.column_stack([safe_predict(library[name], y_train, max(HORIZONS), spec.season) for name in names])
    return panel @ weights


def quantile_bounds(pred: float, residuals: list[float], low: float, high: float) -> tuple[float, float]:
    if len(residuals) < 8:
        spread = max(1.0, abs(pred) * 0.25)
        return max(0.0, pred - spread), pred + spread
    arr = np.asarray(residuals, dtype=float)
    return max(0.0, pred + float(np.quantile(arr, low))), max(0.0, pred + float(np.quantile(arr, high)))


def run_series(spec: SeriesSpec, dates: list[date], y: np.ndarray) -> tuple[list[dict[str, object]], dict[str, object], list[dict[str, object]]]:
    first_origin = len(y) - spec.test_periods
    last_origin = len(y) - max(HORIZONS)
    origins = list(range(first_origin, last_origin + 1))
    rows: list[dict[str, object]] = []
    configs: list[dict[str, object]] = []
    locked_config = choose_config(y, first_origin, spec, True)

    for origin in origins:
        assert origin >= spec.min_train
        current = choose_config(y, origin, spec, True)
        generic = choose_config(y, origin, spec, False)
        configs.append({
            "origin": dates[origin].isoformat(),
            "top_names": current["top_names"],
            "weights": [float(x) for x in current["weights"]],
            "validation_wape": float(current["validation_wape"]),
        })
        y_train = y[:origin]
        full = apply_config(current, y_train, spec)
        no_kg = apply_config(generic, y_train, spec)
        no_exec = apply_config(locked_config, y_train, spec)
        profile_panel = np.column_stack([
            safe_predict(current["library"][name], y_train, max(HORIZONS), spec.season)
            for name in current["top_names"]
        ])
        no_feedback = np.mean(profile_panel, axis=1)
        baseline_library = {
            "last_value": last_value,
            "rolling_mean_4": lambda yy, h, s: rolling_mean(yy, h, s, 4),
            "seasonal_naive": seasonal_naive,
        }
        va, _, vp, vn = validation_panel(y, origin, spec, baseline_library)
        baseline_scores = [(wape(va, vp[:, i]), name) for i, name in enumerate(vn)]
        baseline_name = min(baseline_scores, key=lambda x: (x[0], x[1]))[1]
        no_proposal = safe_predict(baseline_library[baseline_name], y_train, max(HORIZONS), spec.season)
        methods = {
            "full_system": full,
            "ablation_no_kg": no_kg,
            "ablation_locked_execution_config": no_exec,
            "ablation_no_feedback": no_feedback,
            "ablation_no_proposal": no_proposal,
            "baseline_last_value": safe_predict(last_value, y_train, max(HORIZONS), spec.season),
            "baseline_rolling_mean_4": safe_predict(lambda yy, h, s: rolling_mean(yy, h, s, 4), y_train, max(HORIZONS), spec.season),
            "baseline_seasonal_naive": safe_predict(seasonal_naive, y_train, max(HORIZONS), spec.season),
        }
        for h in HORIZONS:
            target_idx = origin + h - 1
            actual = float(y[target_idx])
            for method, prediction in methods.items():
                row = {
                    "series_id": spec.series_id,
                    "country": spec.country,
                    "disease": spec.disease,
                    "metric": spec.metric,
                    "cadence": spec.cadence,
                    "origin_period": dates[origin - 1].isoformat(),
                    "target_period": dates[target_idx].isoformat(),
                    "horizon": h,
                    "method": method,
                    "actual": actual,
                    "prediction": float(prediction[h - 1]),
                    "feature_max_index": origin - 1,
                    "target_index": target_idx,
                }
                if method == "full_system":
                    res = current["residuals"].get(h, [])
                    row["lower_80"], row["upper_80"] = quantile_bounds(float(prediction[h - 1]), res, 0.10, 0.90)
                    row["lower_95"], row["upper_95"] = quantile_bounds(float(prediction[h - 1]), res, 0.025, 0.975)
                else:
                    row["lower_80"] = row["upper_80"] = row["lower_95"] = row["upper_95"] = ""
                rows.append(row)

    threshold = float(np.quantile(y[:first_origin], 0.75))
    grouped: dict[tuple[str, str], list[dict[str, object]]] = {}
    for row in rows:
        grouped.setdefault((str(row["method"]), str(row["origin_period"])), []).append(row)
    warning_rows: list[dict[str, object]] = []
    for (method, origin_period), group in sorted(grouped.items()):
        group.sort(key=lambda r: int(r["horizon"]))
        actual_event = any(float(r["actual"]) >= threshold for r in group)
        predicted_event = any(float(r["prediction"]) >= threshold for r in group)
        first_predicted = next((int(r["horizon"]) for r in group if float(r["prediction"]) >= threshold), None)
        first_actual = next((int(r["horizon"]) for r in group if float(r["actual"]) >= threshold), None)
        warning_rows.append({
            "series_id": spec.series_id,
            "method": method,
            "origin_period": origin_period,
            "threshold": threshold,
            "actual_event": actual_event,
            "predicted_event": predicted_event,
            "first_predicted_horizon": first_predicted if first_predicted is not None else "",
            "first_actual_horizon": first_actual if first_actual is not None else "",
        })
    return rows, {"first_origin_index": first_origin, "origin_count": len(origins), "threshold": threshold, "configs": configs}, warning_rows


def summarize_metrics(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    groups: dict[tuple[str, str, str], list[dict[str, object]]] = {}
    for row in rows:
        groups.setdefault((str(row["series_id"]), str(row["method"]), str(row["horizon"])), []).append(row)
        groups.setdefault((str(row["series_id"]), str(row["method"]), "ALL"), []).append(row)
    output: list[dict[str, object]] = []
    for (series_id, method, horizon), group in sorted(groups.items()):
        actual = np.asarray([float(r["actual"]) for r in group])
        pred = np.asarray([float(r["prediction"]) for r in group])
        m = metric_dict(actual, pred)
        m.update({"series_id": series_id, "method": method, "horizon": horizon})
        if method == "full_system":
            low80 = np.asarray([float(r["lower_80"]) for r in group])
            high80 = np.asarray([float(r["upper_80"]) for r in group])
            low95 = np.asarray([float(r["lower_95"]) for r in group])
            high95 = np.asarray([float(r["upper_95"]) for r in group])
            m["coverage_80"] = float(np.mean((actual >= low80) & (actual <= high80)))
            m["coverage_95"] = float(np.mean((actual >= low95) & (actual <= high95)))
            m["avg_width_80"] = float(np.mean(high80 - low80))
        else:
            m["coverage_80"] = m["coverage_95"] = m["avg_width_80"] = ""
        output.append(m)
    return output


def summarize_warnings(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    groups: dict[tuple[str, str], list[dict[str, object]]] = {}
    for row in rows:
        groups.setdefault((str(row["series_id"]), str(row["method"])), []).append(row)
    output = []
    for (series_id, method), group in sorted(groups.items()):
        tp = sum(bool(r["actual_event"]) and bool(r["predicted_event"]) for r in group)
        fp = sum((not bool(r["actual_event"])) and bool(r["predicted_event"]) for r in group)
        fn = sum(bool(r["actual_event"]) and (not bool(r["predicted_event"])) for r in group)
        tn = len(group) - tp - fp - fn
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        lead = [int(r["first_actual_horizon"]) - int(r["first_predicted_horizon"]) for r in group if r["actual_event"] and r["predicted_event"] and r["first_actual_horizon"] != "" and r["first_predicted_horizon"] != ""]
        output.append({
            "series_id": series_id,
            "method": method,
            "n_origins": len(group),
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "tn": tn,
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "median_lead_periods": float(np.median(lead)) if lead else "",
            "missed_event_rate": fn / (tp + fn) if tp + fn else 0.0,
            "false_alarm_rate": fp / (fp + tn) if fp + tn else 0.0,
        })
    return output


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


all_rows: list[dict[str, object]] = []
all_warning_rows: list[dict[str, object]] = []
input_manifest: dict[str, object] = {}
protocol: dict[str, object] = {
    "seed": SEED,
    "horizons": list(HORIZONS),
    "selection": "origin-safe rolling validation; test labels never used for candidate/weight selection",
    "intervals": "origin-safe empirical residual quantiles from rolling validation only",
    "early_warning_threshold": "fixed 75th percentile of pre-test training observations",
    "ablation_definitions": {
        "no_kg": "generic non-seasonal candidate library",
        "locked_execution_config": "configuration locked at first test origin; origin-safe refits retained",
        "no_feedback": "equal weighting over the full-system top-three validation candidates",
        "no_proposal": "validation-selected baseline-only candidate set",
    },
}

for spec in SPECS:
    dates, values, meta = read_series(spec)
    input_manifest[spec.series_id] = meta
    if len(values) != spec.expected_rows or not meta["unique_dates"] or not meta["sorted_dates"] or not meta["nonnegative_finite"]:
        raise RuntimeError(f"input integrity failed: {spec.series_id}: {meta}")
    rows, run_meta, warnings = run_series(spec, dates, values)
    protocol[spec.series_id] = run_meta
    all_rows.extend(rows)
    all_warning_rows.extend(warnings)

metrics = summarize_metrics(all_rows)
warnings = summarize_warnings(all_warning_rows)
write_csv(OUTPUTS / "paired_predictions.csv", all_rows)
write_csv(OUTPUTS / "metrics.csv", metrics)
write_csv(OUTPUTS / "early_warning.csv", warnings)
(OUTPUTS / "input_manifest.json").write_text(json.dumps(input_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
(OUTPUTS / "protocol.json").write_text(json.dumps(protocol, ensure_ascii=False, indent=2), encoding="utf-8")

overall = {(r["series_id"], r["method"]): r for r in metrics if r["horizon"] == "ALL"}
full_series = [overall[(spec.series_id, "full_system")] for spec in SPECS]
baseline_names = ["baseline_last_value", "baseline_rolling_mean_4", "baseline_seasonal_naive"]
beats_best_baseline = 0
series_gate_rows = []
for spec in SPECS:
    full = overall[(spec.series_id, "full_system")]
    best_name = min(baseline_names, key=lambda name: float(overall[(spec.series_id, name)]["rmse"]))
    best = overall[(spec.series_id, best_name)]
    improved = float(full["rmse"]) < float(best["rmse"])
    beats_best_baseline += int(improved)
    warning = next(r for r in warnings if r["series_id"] == spec.series_id and r["method"] == "full_system")
    series_gate_rows.append({
        "series_id": spec.series_id,
        "full_wape": full["wape"],
        "agreement_pct": full["agreement_pct"],
        "coverage_80": full["coverage_80"],
        "best_baseline": best_name,
        "best_baseline_rmse": best["rmse"],
        "full_rmse": full["rmse"],
        "beats_best_baseline": improved,
        "warning_precision": warning["precision"],
        "warning_recall": warning["recall"],
        "warning_f1": warning["f1"],
    })

ablation_names = ["ablation_no_kg", "ablation_locked_execution_config", "ablation_no_feedback", "ablation_no_proposal"]
macro_wape = {name: float(np.mean([float(overall[(spec.series_id, name)]["wape"]) for spec in SPECS])) for name in ["full_system"] + ablation_names}
ablation_wins = sum(macro_wape["full_system"] < macro_wape[name] for name in ablation_names)
warning_pass_count = sum(r["warning_recall"] >= 0.70 and r["warning_precision"] >= 0.60 for r in series_gate_rows)

gates = {
    "three_distinct_diseases": len({s.disease for s in SPECS}) == 3,
    "all_inputs_complete_and_gap_free": all(m["row_count"] == m["expected_rows"] and m["cadence_gap_count"] == 0 for m in input_manifest.values()),
    "paired_rows_min_48_each": all(int(overall[(s.series_id, "full_system")]["n"]) >= 48 for s in SPECS),
    "no_future_leakage": all(int(r["feature_max_index"]) < int(r["target_index"]) for r in all_rows),
    "full_wape_le_0_30_all_series": all(float(r["full_wape"]) <= 0.30 for r in series_gate_rows),
    "full_beats_best_baseline_at_least_2_of_3": beats_best_baseline >= 2,
    "coverage_80_between_0_70_and_0_90_all_series": all(0.70 <= float(r["coverage_80"]) <= 0.90 for r in series_gate_rows),
    "warning_precision_recall_gate_at_least_2_of_3": warning_pass_count >= 2,
    "full_beats_at_least_3_of_4_ablations_macro_wape": ablation_wins >= 3,
}
passed = all(gates.values())
summary = {
    "status": "TABLE4_FIGURE6_EXPERIMENT_GATES_PASSED" if passed else "TABLE4_FIGURE6_EXPERIMENT_NOT_MET_DOCUMENT_FILL_PAUSED",
    "document_fill_authorized_by_results": passed,
    "series": series_gate_rows,
    "macro_wape": macro_wape,
    "ablation_wins": ablation_wins,
    "warning_gate_pass_count": warning_pass_count,
    "gates": gates,
    "database_writes": 0,
    "forecast_runs_created": 0,
    "promotion_changes": 0,
    "active_pointer_changes": 0,
}
(OUTPUTS / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

report = [
    "# Table 4 / Figure 6 completion experiment",
    "",
    f"Final status: `{summary['status']}`",
    "",
    "## Predeclared interpretation",
    "",
    "The manuscript is changed only if every gate in `summary.json` passes. A failed gate pauses document filling; results remain an auditable negative experiment.",
    "",
    "## Series results",
    "",
    "| Series | WAPE | Agreement | 80% coverage | Full RMSE | Best baseline RMSE | Better? | Warning P/R/F1 |",
    "|---|---:|---:|---:|---:|---:|:---:|---:|",
]
for row in series_gate_rows:
    report.append(
        f"| {row['series_id']} | {row['full_wape']:.4f} | {row['agreement_pct']:.2f}% | {row['coverage_80']:.3f} | "
        f"{row['full_rmse']:.4f} | {row['best_baseline_rmse']:.4f} | {'YES' if row['beats_best_baseline'] else 'NO'} | "
        f"{row['warning_precision']:.2f}/{row['warning_recall']:.2f}/{row['warning_f1']:.2f} |"
    )
report += ["", "## Gate results", ""]
for name, value in gates.items():
    report.append(f"- `{name}`: {'PASS' if value else 'FAIL'}")
report += [
    "",
    "## Safety boundaries",
    "",
    "- Inputs were exported in a REPEATABLE READ READ ONLY transaction.",
    "- No database write, forecast publication, promotion or active pointer change was performed.",
    "- No GPU, Qwen, vLLM or external network request was used.",
]
(OUTPUTS / "FINAL_REPORT.md").write_text("\n".join(report) + "\n", encoding="utf-8")

manifest_lines = []
for path in sorted(OUTPUTS.iterdir()):
    if path.is_file() and path.name != "MANIFEST.sha256":
        manifest_lines.append(f"{sha256(path)}  {path.name}")
(OUTPUTS / "MANIFEST.sha256").write_text("\n".join(manifest_lines) + "\n", encoding="utf-8")

print(json.dumps(summary, ensure_ascii=False))
