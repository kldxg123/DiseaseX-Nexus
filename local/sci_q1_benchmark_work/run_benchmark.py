from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import json
import math
import os
import platform
import random
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
import scipy
from scipy.optimize import minimize
from sklearn import __version__ as sklearn_version
from sklearn.ensemble import (
    ExtraTreesRegressor,
    GradientBoostingRegressor,
    HistGradientBoostingRegressor,
    RandomForestRegressor,
)
from sklearn.linear_model import PoissonRegressor, Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


SEED = 20260822
TRAIN_N = 799
VALIDATION_N = 26
TEST_N = 13
MAX_HORIZON = 26
MAX_LAG = 104
REPOSITORY_COMMIT = "3740776f65c6f4157e67e6a78f71daeade45246b"
EXPECTED_WORKBOOK_SHA256 = "19b55bfcede7539fd15087f21d0b07e9d2e78dd9b19c6584057fecc721cd325a"
EXPECTED_CSV_SHA256 = "86f057db4b7e0f56ec5d73b86f90738d2450b92fbd4c1a3109e0cb06e4f025a9"


@dataclass(frozen=True)
class Fold:
    name: str
    origin: int
    horizon: int
    weight_group: str


@dataclass(frozen=True)
class ModelSpec:
    name: str
    family: str
    params: dict[str, Any]


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def metrics(actual: np.ndarray, predicted: np.ndarray) -> dict[str, float]:
    actual = np.asarray(actual, dtype=float)
    predicted = np.asarray(predicted, dtype=float)
    error = predicted - actual
    denominator = float(np.abs(actual).sum())
    wape = float(np.abs(error).sum() / denominator) if denominator else float("nan")
    smape_den = np.abs(actual) + np.abs(predicted)
    smape = float(np.mean(np.where(smape_den > 0, 2 * np.abs(error) / smape_den, 0.0)))
    return {
        "wape": wape,
        "agreement": max(0.0, 1.0 - wape),
        "mae": float(np.mean(np.abs(error))),
        "rmse": float(np.sqrt(np.mean(np.square(error)))),
        "smape": smape,
    }


def load_data(workbook: Path, output_dir: Path) -> pd.DataFrame:
    workbook_sha = sha256_file(workbook)
    if workbook_sha != EXPECTED_WORKBOOK_SHA256:
        raise RuntimeError(f"workbook SHA mismatch: {workbook_sha}")
    frame = pd.read_excel(workbook, sheet_name="in")
    frame.columns = [str(item).strip().lower() for item in frame.columns]
    required = ["year", "week", "cases"]
    if list(frame.columns) != required:
        raise RuntimeError(f"unexpected workbook columns: {frame.columns.tolist()}")
    frame = frame.astype({"year": "int64", "week": "int64", "cases": "float64"})
    if len(frame) != TRAIN_N + VALIDATION_N + TEST_N:
        raise RuntimeError(f"unexpected row count: {len(frame)}")
    if frame.isna().any().any() or (frame["cases"] < 0).any():
        raise RuntimeError("invalid missing or negative cases")
    csv_path = output_dir / "singapore_dengue_epicasting.csv"
    frame.to_csv(csv_path, index=False, lineterminator="\n")
    # The prior canonical CSV used integer cases and title-case columns. Preserve a second
    # canonical export to independently revalidate its historical hash.
    canonical = frame.rename(columns={"year": "Year", "week": "Week", "cases": "Cases"}).copy()
    canonical["Cases"] = canonical["Cases"].astype(int)
    canonical_path = output_dir / "singapore_dengue_epicasting_historical_format.csv"
    canonical.to_csv(canonical_path, index=False, lineterminator="\n")
    write_json(
        output_dir / "data_identity.json",
        {
            "workbook": str(workbook),
            "workbook_sha256": workbook_sha,
            "expected_workbook_sha256": EXPECTED_WORKBOOK_SHA256,
            "derived_csv": str(csv_path),
            "derived_csv_sha256": sha256_file(csv_path),
            "historical_canonical_expected_sha256": EXPECTED_CSV_SHA256,
            "historical_format_csv_sha256": sha256_file(canonical_path),
            "rows": len(frame),
            "first_key": {"year": int(frame.iloc[0].year), "week": int(frame.iloc[0].week)},
            "last_key": {"year": int(frame.iloc[-1].year), "week": int(frame.iloc[-1].week)},
        },
    )
    return frame


def key_index(frame: pd.DataFrame, year: int, week: int) -> int:
    matches = frame.index[(frame["year"] == year) & (frame["week"] == week)].tolist()
    if len(matches) != 1:
        raise RuntimeError(f"expected one row for {year}-W{week:02d}, got {len(matches)}")
    return int(matches[0])


def build_folds(frame: pd.DataFrame) -> list[Fold]:
    folds: list[Fold] = []
    for year in range(2010, 2015):
        idx = key_index(frame, year, 40)
        origin = idx + 1
        if origin + 13 > TRAIN_N:
            raise RuntimeError("seasonal tuning fold reaches paper validation/test")
        folds.append(Fold(f"season_{year}_w41_w53", origin, 13, "season"))
    folds.append(Fold("paper_validation_2015_w15_w40", TRAIN_N, VALIDATION_N, "paper_validation"))
    return folds


def slope(values: np.ndarray) -> float:
    if len(values) < 2:
        return 0.0
    x = np.arange(len(values), dtype=float)
    x -= x.mean()
    denom = float(np.dot(x, x))
    return float(np.dot(x, values - values.mean()) / denom) if denom else 0.0


def origin_features(
    y: np.ndarray,
    frame: pd.DataFrame,
    origin: int,
    horizon: int,
) -> np.ndarray:
    if origin < MAX_LAG or origin + horizon - 1 >= len(frame):
        raise ValueError("origin/horizon outside feature domain")
    history = y[:origin]
    target_idx = origin + horizon - 1
    lags = [1, 2, 3, 4, 6, 8, 13, 26, 52, 53, 78, 104]
    values: list[float] = [float(history[-lag]) for lag in lags]
    values.extend(float(np.log1p(max(history[-lag], 0.0))) for lag in lags)
    for window in [4, 8, 13, 26, 52, 104]:
        segment = history[-window:]
        values.extend(
            [
                float(segment.mean()),
                float(segment.std(ddof=0)),
                float(segment.min()),
                float(segment.max()),
                slope(segment),
            ]
        )
    target_week = float(frame.iloc[target_idx]["week"])
    origin_week = float(frame.iloc[origin - 1]["week"])
    for harmonic in [1, 2, 3, 4]:
        values.extend(
            [
                math.sin(2 * math.pi * harmonic * target_week / 52.1775),
                math.cos(2 * math.pi * harmonic * target_week / 52.1775),
                math.sin(2 * math.pi * harmonic * origin_week / 52.1775),
                math.cos(2 * math.pi * harmonic * origin_week / 52.1775),
            ]
        )
    values.extend(
        [
            float(horizon),
            math.log1p(float(horizon)),
            float(origin) / 52.1775,
            float(target_idx) / 52.1775,
            float(frame.iloc[target_idx]["year"] - 2000),
            target_week,
        ]
    )
    return np.asarray(values, dtype=float)


def pooled_training_matrix(
    y: np.ndarray,
    frame: pd.DataFrame,
    n_observed: int,
    max_horizon: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    xs: list[np.ndarray] = []
    ys: list[float] = []
    ages: list[float] = []
    stride = 1
    for origin in range(MAX_LAG, n_observed, stride):
        available = min(max_horizon, n_observed - origin)
        for horizon in range(1, available + 1):
            xs.append(origin_features(y, frame, origin, horizon))
            target_idx = origin + horizon - 1
            ys.append(float(y[target_idx]))
            ages.append(float(n_observed - 1 - target_idx))
    if not xs:
        raise RuntimeError("empty pooled training matrix")
    return np.vstack(xs), np.asarray(ys), np.asarray(ages)


def make_ml_specs() -> list[ModelSpec]:
    specs: list[ModelSpec] = []
    for alpha, transform, half_life in itertools.product(
        [0.1, 1.0, 10.0, 100.0], ["raw", "log1p"], [104.0, 260.0]
    ):
        specs.append(ModelSpec(f"ridge_a{alpha:g}_{transform}_hl{int(half_life)}", "ridge", {"alpha": alpha, "transform": transform, "half_life": half_life}))
    for alpha, half_life in itertools.product([0.01, 0.1, 1.0], [104.0, 260.0]):
        specs.append(ModelSpec(f"poisson_a{alpha:g}_hl{int(half_life)}", "poisson", {"alpha": alpha, "half_life": half_life, "transform": "raw"}))
    hist_configs = [
        (0.03, 15, 10, 1.0, "raw", 104.0),
        (0.05, 15, 10, 10.0, "raw", 104.0),
        (0.05, 31, 20, 1.0, "raw", 260.0),
        (0.08, 15, 20, 10.0, "raw", 260.0),
        (0.03, 15, 10, 1.0, "log1p", 104.0),
        (0.05, 31, 20, 10.0, "log1p", 260.0),
    ]
    for lr, leaves, leaf_n, l2, transform, half_life in hist_configs:
        name = f"hist_lr{lr:g}_leaves{leaves}_leaf{leaf_n}_l2{l2:g}_{transform}_hl{int(half_life)}"
        specs.append(ModelSpec(name, "hist", {"learning_rate": lr, "max_leaf_nodes": leaves, "min_samples_leaf": leaf_n, "l2_regularization": l2, "transform": transform, "half_life": half_life}))
    tree_configs = [
        ("extra_trees", 8, 3, 0.8, "raw", 104.0),
        ("extra_trees", 12, 5, 1.0, "raw", 260.0),
        ("extra_trees", None, 8, 0.8, "log1p", 260.0),
        ("random_forest", 8, 3, 0.8, "raw", 104.0),
        ("random_forest", 12, 5, 1.0, "raw", 260.0),
        ("random_forest", None, 8, 0.8, "log1p", 260.0),
    ]
    for family, depth, leaf_n, max_features, transform, half_life in tree_configs:
        d = "none" if depth is None else str(depth)
        name = f"{family}_d{d}_leaf{leaf_n}_f{max_features:g}_{transform}_hl{int(half_life)}"
        specs.append(ModelSpec(name, family, {"max_depth": depth, "min_samples_leaf": leaf_n, "max_features": max_features, "transform": transform, "half_life": half_life}))
    gbr_configs = [
        (0.03, 2, 5, "huber", "raw", 104.0),
        (0.05, 2, 10, "huber", "raw", 260.0),
        (0.03, 3, 10, "squared_error", "raw", 260.0),
        (0.03, 2, 5, "huber", "log1p", 104.0),
    ]
    for lr, depth, leaf_n, loss, transform, half_life in gbr_configs:
        name = f"gbr_lr{lr:g}_d{depth}_leaf{leaf_n}_{loss}_{transform}_hl{int(half_life)}"
        specs.append(ModelSpec(name, "gbr", {"learning_rate": lr, "max_depth": depth, "min_samples_leaf": leaf_n, "loss": loss, "transform": transform, "half_life": half_life}))
    return specs


def build_estimator(spec: ModelSpec) -> Any:
    p = spec.params
    if spec.family == "ridge":
        return Pipeline([("scale", StandardScaler()), ("model", Ridge(alpha=float(p["alpha"])))])
    if spec.family == "poisson":
        return Pipeline([("scale", StandardScaler()), ("model", PoissonRegressor(alpha=float(p["alpha"]), max_iter=1000))])
    if spec.family == "hist":
        return HistGradientBoostingRegressor(
            learning_rate=float(p["learning_rate"]),
            max_iter=220,
            max_leaf_nodes=int(p["max_leaf_nodes"]),
            min_samples_leaf=int(p["min_samples_leaf"]),
            l2_regularization=float(p["l2_regularization"]),
            random_state=SEED,
        )
    common = {
        "n_estimators": 240,
        "max_depth": p["max_depth"],
        "min_samples_leaf": int(p["min_samples_leaf"]),
        "max_features": float(p["max_features"]),
        "random_state": SEED,
        "n_jobs": 4,
    }
    if spec.family == "extra_trees":
        return ExtraTreesRegressor(**common)
    if spec.family == "random_forest":
        return RandomForestRegressor(**common)
    if spec.family == "gbr":
        return GradientBoostingRegressor(
            n_estimators=220,
            learning_rate=float(p["learning_rate"]),
            max_depth=int(p["max_depth"]),
            min_samples_leaf=int(p["min_samples_leaf"]),
            loss=str(p["loss"]),
            random_state=SEED,
        )
    raise ValueError(f"unknown family: {spec.family}")


def fit_predict_ml(
    spec: ModelSpec,
    y: np.ndarray,
    frame: pd.DataFrame,
    origin: int,
    horizon: int,
) -> np.ndarray:
    x_train, y_train, ages = pooled_training_matrix(y, frame, origin, MAX_HORIZON)
    x_future = np.vstack([origin_features(y, frame, origin, h) for h in range(1, horizon + 1)])
    transform = str(spec.params.get("transform", "raw"))
    target = np.log1p(y_train) if transform == "log1p" else y_train
    half_life = float(spec.params.get("half_life", 260.0))
    weights = np.power(0.5, ages / half_life)
    estimator = build_estimator(spec)
    if isinstance(estimator, Pipeline):
        estimator.fit(x_train, target, model__sample_weight=weights)
    else:
        estimator.fit(x_train, target, sample_weight=weights)
    predicted = np.asarray(estimator.predict(x_future), dtype=float)
    if transform == "log1p":
        predicted = np.expm1(predicted)
    return np.clip(predicted, 0.0, None)


def seasonal_values(
    y: np.ndarray,
    frame: pd.DataFrame,
    origin: int,
    target_indices: Iterable[int],
    years_back: int,
    statistic: str,
) -> np.ndarray:
    rows = frame.iloc[:origin]
    result: list[float] = []
    target_year = int(frame.iloc[origin]["year"])
    min_year = target_year - years_back
    for idx in target_indices:
        week = int(frame.iloc[idx]["week"])
        mask = (rows["week"] == week) & (rows["year"] >= min_year)
        values = y[:origin][mask.to_numpy()]
        if len(values) == 0 and week == 53:
            mask = (rows["week"] == 52) & (rows["year"] >= min_year)
            values = y[:origin][mask.to_numpy()]
        if len(values) == 0:
            values = y[max(0, origin - 52) : origin]
        value = float(np.median(values) if statistic == "median" else np.mean(values))
        result.append(value)
    return np.asarray(result)


def predict_seasonal_scaled(
    y: np.ndarray,
    frame: pd.DataFrame,
    origin: int,
    horizon: int,
    years_back: int,
    statistic: str,
    scale_window: int,
) -> np.ndarray:
    targets = list(range(origin, origin + horizon))
    base = seasonal_values(y, frame, origin, targets, years_back, statistic)
    hist_indices = list(range(max(0, origin - scale_window), origin))
    hist_base = seasonal_values(y, frame, origin, hist_indices, years_back, statistic)
    denom = float(np.sum(np.abs(hist_base)))
    ratio = float(np.sum(y[hist_indices]) / denom) if denom > 0 else 1.0
    ratio = float(np.clip(ratio, 0.45, 2.25))
    return np.clip(base * ratio, 0.0, None)


def predict_analog(
    y: np.ndarray,
    origin: int,
    horizon: int,
    context: int,
    neighbors: int,
) -> np.ndarray:
    query = y[origin - context : origin]
    q_mean = float(query.mean())
    q_scale = float(query.std(ddof=0)) or max(q_mean, 1.0)
    query_norm = (query - q_mean) / q_scale
    candidates: list[tuple[float, np.ndarray]] = []
    min_end = context
    max_end = origin - horizon
    for end in range(min_end, max_end + 1):
        context_values = y[end - context : end]
        c_mean = float(context_values.mean())
        c_scale = float(context_values.std(ddof=0)) or max(c_mean, 1.0)
        distance = float(np.mean(np.square(query_norm - (context_values - c_mean) / c_scale)))
        future = y[end : end + horizon]
        scaled_future = future * (q_mean / max(c_mean, 1.0))
        candidates.append((distance, scaled_future))
    candidates.sort(key=lambda item: item[0])
    chosen = candidates[:neighbors]
    if not chosen:
        return np.repeat(y[origin - 1], horizon)
    inv = np.asarray([1.0 / max(item[0], 1e-6) for item in chosen])
    inv /= inv.sum()
    matrix = np.vstack([item[1] for item in chosen])
    return np.clip(np.average(matrix, axis=0, weights=inv), 0.0, None)


def deterministic_specs() -> list[ModelSpec]:
    specs = [
        ModelSpec("last_value", "last_value", {}),
        ModelSpec("rolling_mean_4", "rolling_mean", {"window": 4}),
        ModelSpec("rolling_mean_8", "rolling_mean", {"window": 8}),
        ModelSpec("seasonal_naive_52", "seasonal_naive", {"lag": 52}),
    ]
    for years_back, statistic, scale_window in itertools.product([3, 5, 8, 12], ["mean", "median"], [8, 13, 26]):
        name = f"seasonal_scaled_y{years_back}_{statistic}_w{scale_window}"
        specs.append(ModelSpec(name, "seasonal_scaled", {"years_back": years_back, "statistic": statistic, "scale_window": scale_window}))
    for context, neighbors in itertools.product([8, 13, 26], [3, 5, 10]):
        specs.append(ModelSpec(f"analog_c{context}_k{neighbors}", "analog", {"context": context, "neighbors": neighbors}))
    return specs


def predict_spec(spec: ModelSpec, y: np.ndarray, frame: pd.DataFrame, origin: int, horizon: int) -> np.ndarray:
    if spec.family in {"ridge", "poisson", "hist", "extra_trees", "random_forest", "gbr"}:
        return fit_predict_ml(spec, y, frame, origin, horizon)
    if spec.family == "last_value":
        return np.repeat(y[origin - 1], horizon).astype(float)
    if spec.family == "rolling_mean":
        window = int(spec.params["window"])
        return np.repeat(float(y[origin - window : origin].mean()), horizon)
    if spec.family == "seasonal_naive":
        lag = int(spec.params["lag"])
        return np.asarray([y[origin + h - lag] for h in range(horizon)], dtype=float)
    if spec.family == "seasonal_scaled":
        return predict_seasonal_scaled(y, frame, origin, horizon, int(spec.params["years_back"]), str(spec.params["statistic"]), int(spec.params["scale_window"]))
    if spec.family == "analog":
        return predict_analog(y, origin, horizon, int(spec.params["context"]), int(spec.params["neighbors"]))
    raise ValueError(f"unknown model family: {spec.family}")


def prediction_sha(predicted: np.ndarray) -> str:
    normalized = [format(float(value), ".12f") for value in predicted]
    return canonical_sha256(normalized)


def tune(frame: pd.DataFrame, output_dir: Path) -> None:
    y = frame["cases"].to_numpy(dtype=float)
    folds = build_folds(frame)
    specs = deterministic_specs() + make_ml_specs()
    spec_by_name = {spec.name: spec for spec in specs}
    prediction_rows: list[dict[str, Any]] = []
    search_rows: list[dict[str, Any]] = []
    predictions: dict[str, dict[str, np.ndarray]] = {}
    for spec_no, spec in enumerate(specs, start=1):
        print(f"TUNE {spec_no}/{len(specs)} {spec.name}", flush=True)
        predictions[spec.name] = {}
        failed: str | None = None
        for fold in folds:
            try:
                predicted = predict_spec(spec, y, frame, fold.origin, fold.horizon)
            except Exception as exc:  # preserve failed candidate without hiding it
                failed = f"{type(exc).__name__}: {exc}"
                break
            predictions[spec.name][fold.name] = predicted
        if failed:
            search_rows.append({"model": spec.name, "family": spec.family, "status": "FAILED", "error": failed})
            continue
        group_actual: dict[str, list[float]] = {"season": [], "paper_validation": []}
        group_predicted: dict[str, list[float]] = {"season": [], "paper_validation": []}
        for fold in folds:
            actual = y[fold.origin : fold.origin + fold.horizon]
            predicted = predictions[spec.name][fold.name]
            group_actual[fold.weight_group].extend(actual.tolist())
            group_predicted[fold.weight_group].extend(predicted.tolist())
            for h, (actual_value, predicted_value) in enumerate(zip(actual, predicted, strict=True), start=1):
                idx = fold.origin + h - 1
                prediction_rows.append(
                    {
                        "fold": fold.name,
                        "group": fold.weight_group,
                        "model": spec.name,
                        "family": spec.family,
                        "horizon": h,
                        "row_index": idx + 1,
                        "year": int(frame.iloc[idx]["year"]),
                        "week": int(frame.iloc[idx]["week"]),
                        "actual": float(actual_value),
                        "prediction": float(predicted_value),
                    }
                )
        season_metrics = metrics(np.asarray(group_actual["season"]), np.asarray(group_predicted["season"]))
        validation_metrics = metrics(np.asarray(group_actual["paper_validation"]), np.asarray(group_predicted["paper_validation"]))
        selection_score = 0.70 * season_metrics["wape"] + 0.30 * validation_metrics["wape"]
        search_rows.append(
            {
                "model": spec.name,
                "family": spec.family,
                "status": "PASS",
                "season_wape": season_metrics["wape"],
                "validation_wape": validation_metrics["wape"],
                "selection_score": selection_score,
                "season_mae": season_metrics["mae"],
                "validation_mae": validation_metrics["mae"],
                "params_json": json.dumps(spec.params, sort_keys=True),
            }
        )
    search = pd.DataFrame(search_rows)
    search.to_csv(output_dir / "model_search.csv", index=False, lineterminator="\n")
    pd.DataFrame(prediction_rows).to_csv(output_dir / "tuning_predictions.csv", index=False, lineterminator="\n")
    passed = search[search["status"] == "PASS"].sort_values("selection_score", kind="stable")
    if len(passed) < 3:
        raise RuntimeError("fewer than three tuning candidates passed")

    top_names = passed.head(5)["model"].tolist()
    fold_names = [fold.name for fold in folds]
    actual_all = np.concatenate([y[fold.origin : fold.origin + fold.horizon] for fold in folds])
    matrix = np.column_stack([np.concatenate([predictions[name][fold] for fold in fold_names]) for name in top_names])
    season_count = sum(fold.horizon for fold in folds if fold.weight_group == "season")
    row_weights = np.concatenate(
        [
            np.repeat(0.70 / season_count, season_count),
            np.repeat(0.30 / VALIDATION_N, VALIDATION_N),
        ]
    )

    def objective(weights: np.ndarray) -> float:
        pred = matrix @ weights
        return float(np.sum(row_weights * np.abs(actual_all - pred)))

    start = np.repeat(1.0 / len(top_names), len(top_names))
    opt = minimize(
        objective,
        start,
        method="SLSQP",
        bounds=[(0.0, 1.0)] * len(top_names),
        constraints={"type": "eq", "fun": lambda value: float(np.sum(value) - 1.0)},
        options={"maxiter": 1000, "ftol": 1e-12},
    )
    if not opt.success:
        raise RuntimeError(f"ensemble optimization failed: {opt.message}")
    ensemble_weights = np.asarray(opt.x)
    ensemble_pred = matrix @ ensemble_weights
    season_actual = actual_all[:season_count]
    season_pred = ensemble_pred[:season_count]
    validation_actual = actual_all[season_count:]
    validation_pred = ensemble_pred[season_count:]
    ensemble_score = 0.70 * metrics(season_actual, season_pred)["wape"] + 0.30 * metrics(validation_actual, validation_pred)["wape"]
    best_individual_score = float(passed.iloc[0]["selection_score"])
    use_ensemble = ensemble_score + 1e-12 < best_individual_score
    if use_ensemble:
        selected_components = [
            {"model": name, "weight": float(weight), "spec": asdict(spec_by_name[name])}
            for name, weight in zip(top_names, ensemble_weights, strict=True)
            if weight > 1e-6
        ]
        selected_name = "kg_validation_only_convex_ensemble.v2"
        selected_score = ensemble_score
    else:
        name = str(passed.iloc[0]["model"])
        selected_components = [{"model": name, "weight": 1.0, "spec": asdict(spec_by_name[name])}]
        selected_name = "kg_best_validated_candidate.v2"
        selected_score = best_individual_score

    lock = {
        "status": "LOCKED_BEFORE_TEST",
        "created_at": utc_now(),
        "seed": SEED,
        "paper_test_labels_read_during_selection": False,
        "selection_protocol": {
            "seasonally_matched_rolling_origins": [asdict(fold) for fold in folds if fold.weight_group == "season"],
            "paper_validation_fold": asdict(folds[-1]),
            "objective": "0.70 * aggregate WAPE on 2010-2014 W41-W53 + 0.30 * WAPE on paper validation 2015 W15-W40",
            "candidate_count": len(specs),
            "passed_candidate_count": int(len(passed)),
            "ensemble_pool": top_names,
        },
        "selected_method": selected_name,
        "selected_score": selected_score,
        "selected_components": selected_components,
        "top_individual_candidates": passed.head(15).to_dict(orient="records"),
        "ensemble_tuning": {
            "used": use_ensemble,
            "pool": top_names,
            "weights": {name: float(weight) for name, weight in zip(top_names, ensemble_weights, strict=True)},
            "score": ensemble_score,
        },
        "data_sha256": sha256_file(output_dir / "singapore_dengue_epicasting.csv"),
        "repository_commit": REPOSITORY_COMMIT,
    }
    lock["lock_sha256"] = canonical_sha256(lock)
    write_json(output_dir / "locked_selection.json", lock)
    print(f"LOCKED_SELECTION={selected_name} SCORE={selected_score:.8f}", flush=True)


def load_spec(component: dict[str, Any]) -> ModelSpec:
    raw = component["spec"]
    return ModelSpec(name=str(raw["name"]), family=str(raw["family"]), params=dict(raw["params"]))


def predict_locked(lock: dict[str, Any], y: np.ndarray, frame: pd.DataFrame, origin: int, horizon: int) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    component_predictions: dict[str, np.ndarray] = {}
    combined = np.zeros(horizon, dtype=float)
    for component in lock["selected_components"]:
        spec = load_spec(component)
        predicted = predict_spec(spec, y, frame, origin, horizon)
        component_predictions[spec.name] = predicted
        combined += float(component["weight"]) * predicted
    return np.clip(combined, 0.0, None), component_predictions


def evaluate(frame: pd.DataFrame, output_dir: Path, prior_report_dir: Path) -> None:
    consumed_path = output_dir / "test_consumed.json"
    if consumed_path.exists():
        raise RuntimeError("TEST_ALREADY_CONSUMED")
    lock = json.loads((output_dir / "locked_selection.json").read_text(encoding="utf-8"))
    lock_copy = dict(lock)
    expected_lock_sha = lock_copy.pop("lock_sha256")
    if canonical_sha256(lock_copy) != expected_lock_sha:
        raise RuntimeError("selection lock hash mismatch")
    y = frame["cases"].to_numpy(dtype=float)
    origin = TRAIN_N + VALIDATION_N
    predicted, components = predict_locked(lock, y, frame, origin, TEST_N)
    pre_actual_prediction_sha = prediction_sha(predicted)

    # Future-label sentinel: replacing hidden labels must not alter model inputs or predictions.
    sentinel_y = y.copy()
    sentinel_y[origin:] = sentinel_y[origin:] + np.linspace(10000.0, 20000.0, TEST_N)
    sentinel_predicted, _ = predict_locked(lock, sentinel_y, frame, origin, TEST_N)
    sentinel_sha = prediction_sha(sentinel_predicted)
    if sentinel_sha != pre_actual_prediction_sha:
        raise RuntimeError("future-label sentinel changed predictions")

    # Deterministic repeat with identical seed and inputs.
    repeat_predicted, _ = predict_locked(lock, y, frame, origin, TEST_N)
    repeat_sha = prediction_sha(repeat_predicted)
    if repeat_sha != pre_actual_prediction_sha:
        raise RuntimeError("deterministic repeat mismatch")

    actual = y[origin : origin + TEST_N]
    paper = pd.read_csv(prior_report_dir / "ewnet_predictions.csv")
    if len(paper) != TEST_N or not np.array_equal(paper["actual"].to_numpy(dtype=float), actual):
        raise RuntimeError("EWNet pairing mismatch")
    paper_pred = paper["prediction"].to_numpy(dtype=float)
    baselines: dict[str, np.ndarray] = {
        "last_value": predict_spec(ModelSpec("last_value", "last_value", {}), y, frame, origin, TEST_N),
        "rolling_mean_4": predict_spec(ModelSpec("rolling_mean_4", "rolling_mean", {"window": 4}), y, frame, origin, TEST_N),
        "seasonal_naive_52": predict_spec(ModelSpec("seasonal_naive_52", "seasonal_naive", {"lag": 52}), y, frame, origin, TEST_N),
    }
    methods: dict[str, np.ndarray] = {
        "EWNet_author_code": paper_pred,
        str(lock["selected_method"]): predicted,
        **baselines,
    }
    overall_rows = []
    for name, values in methods.items():
        row = {"method": name, **metrics(actual, values), "prediction_sha256": prediction_sha(values), "n": TEST_N}
        overall_rows.append(row)
    overall = pd.DataFrame(overall_rows).sort_values("wape", kind="stable")
    overall.to_csv(output_dir / "test_metrics.csv", index=False, lineterminator="\n")

    paired_rows: list[dict[str, Any]] = []
    for h in range(1, TEST_N + 1):
        idx = origin + h - 1
        row: dict[str, Any] = {
            "horizon": h,
            "row_index": idx + 1,
            "year": int(frame.iloc[idx]["year"]),
            "week": int(frame.iloc[idx]["week"]),
            "actual": float(actual[h - 1]),
        }
        for name, values in methods.items():
            row[name] = float(values[h - 1])
        paired_rows.append(row)
    pd.DataFrame(paired_rows).to_csv(output_dir / "test_paired_predictions.csv", index=False, lineterminator="\n")

    selected_metrics = metrics(actual, predicted)
    paper_metrics = metrics(actual, paper_pred)
    baseline_metrics = {name: metrics(actual, values) for name, values in baselines.items()}
    comparison = {
        "selected_vs_ewnet_relative_wape_change": (selected_metrics["wape"] - paper_metrics["wape"]) / paper_metrics["wape"],
        "selected_vs_best_baseline": None,
        "selected_beats_ewnet": selected_metrics["wape"] < paper_metrics["wape"],
        "selected_beats_best_baseline": selected_metrics["wape"] < min(value["wape"] for value in baseline_metrics.values()),
    }
    best_baseline_name = min(baseline_metrics, key=lambda key: baseline_metrics[key]["wape"])
    comparison["selected_vs_best_baseline"] = {
        "baseline": best_baseline_name,
        "relative_wape_change": (selected_metrics["wape"] - baseline_metrics[best_baseline_name]["wape"]) / baseline_metrics[best_baseline_name]["wape"],
    }
    consumed = {
        "status": "RETROSPECTIVE_TEST_EXECUTED_ONCE",
        "consumed_at": utc_now(),
        "lock_sha256": expected_lock_sha,
        "prediction_sha256_before_reading_actual": pre_actual_prediction_sha,
        "future_label_sentinel_prediction_sha256": sentinel_sha,
        "deterministic_repeat_prediction_sha256": repeat_sha,
        "future_label_sentinel_pass": True,
        "deterministic_repeat_pass": True,
        "test_rows": TEST_N,
        "selected_metrics": selected_metrics,
        "paper_ewnet_metrics": paper_metrics,
        "baseline_metrics": baseline_metrics,
        "comparison": comparison,
        "component_prediction_sha256": {name: prediction_sha(values) for name, values in components.items()},
    }
    write_json(consumed_path, consumed)
    write_json(
        output_dir / "runtime_identity.json",
        {
            "python": sys.version,
            "platform": platform.platform(),
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "scipy": scipy.__version__,
            "scikit_learn": sklearn_version,
            "seed": SEED,
            "cpu_count": os.cpu_count(),
        },
    )

    final = {
        "status": "SCI_Q1_EWNET_COMPARISON_OPTIMIZED_RETROSPECTIVE_VERIFIED",
        "paper": {
            "title": "Epicasting: An Ensemble Wavelet Neural Network for forecasting epidemics",
            "journal": "Neural Networks",
            "year": 2023,
            "doi": "10.1016/j.neunet.2023.05.049",
            "tier": "SCIE; JCR 2024 Q1 in Computer Science, Artificial Intelligence and Neurosciences",
            "author_repository_commit": REPOSITORY_COMMIT,
        },
        "data": {
            "dataset": "Singapore dengue weekly cases",
            "rows": len(frame),
            "split": {"train": TRAIN_N, "validation": VALIDATION_N, "test": TEST_N},
            "workbook_sha256": EXPECTED_WORKBOOK_SHA256,
        },
        "selection": lock,
        "test": consumed,
        "governance": {
            "retrospective_not_fresh": True,
            "test_used_for_selection": False,
            "database_writes": 0,
            "forecast_runs_created": 0,
            "promotion": 0,
            "active_pointer_changes": 0,
            "dashboard_changes": 0,
        },
    }
    write_json(output_dir / "final_summary.json", final)
    print(json.dumps({"status": final["status"], "selected_metrics": selected_metrics, "comparison": comparison}, indent=2), flush=True)


def verify(output_dir: Path) -> None:
    required = [
        "data_identity.json",
        "model_search.csv",
        "tuning_predictions.csv",
        "locked_selection.json",
        "test_consumed.json",
        "test_metrics.csv",
        "test_paired_predictions.csv",
        "runtime_identity.json",
        "final_summary.json",
    ]
    missing = [name for name in required if not (output_dir / name).is_file()]
    if missing:
        raise RuntimeError(f"missing outputs: {missing}")
    lock = json.loads((output_dir / "locked_selection.json").read_text(encoding="utf-8"))
    check = dict(lock)
    expected = check.pop("lock_sha256")
    if canonical_sha256(check) != expected:
        raise RuntimeError("lock hash verification failed")
    consumed = json.loads((output_dir / "test_consumed.json").read_text(encoding="utf-8"))
    if not consumed["future_label_sentinel_pass"] or not consumed["deterministic_repeat_pass"]:
        raise RuntimeError("sentinel/repeat gate failed")
    if consumed["lock_sha256"] != expected:
        raise RuntimeError("consumed result not bound to selection lock")
    manifest_rows = []
    for path in sorted(output_dir.iterdir()):
        if path.is_file() and path.name != "MANIFEST.sha256":
            manifest_rows.append(f"{sha256_file(path)}  {path.name}")
    (output_dir / "MANIFEST.sha256").write_text("\n".join(manifest_rows) + "\n", encoding="utf-8")
    print(f"VERIFY_PASS files={len(manifest_rows)} lock_sha256={expected}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["tune", "evaluate", "verify"])
    parser.add_argument("--experiment-root", type=Path, required=True)
    parser.add_argument("--prior-report-dir", type=Path)
    args = parser.parse_args()
    random.seed(SEED)
    np.random.seed(SEED)
    root = args.experiment_root.resolve()
    output_dir = root / "outputs"
    output_dir.mkdir(parents=True, exist_ok=True)
    workbook = root / "Epicasting/Datasets/Dengue_datasets/Singapore_Dengue.xlsx"
    frame = load_data(workbook, output_dir)
    if args.stage == "tune":
        if (output_dir / "locked_selection.json").exists():
            raise RuntimeError("SELECTION_ALREADY_LOCKED")
        tune(frame, output_dir)
    elif args.stage == "evaluate":
        if args.prior_report_dir is None:
            raise RuntimeError("--prior-report-dir is required for evaluation")
        evaluate(frame, output_dir, args.prior_report_dir.resolve())
    else:
        verify(output_dir)


if __name__ == "__main__":
    main()
