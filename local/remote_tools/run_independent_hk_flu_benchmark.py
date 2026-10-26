from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import zipfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import (
    ExtraTreesRegressor,
    GradientBoostingRegressor,
    HistGradientBoostingRegressor,
    RandomForestRegressor,
)
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler


SEED = 20260825
VALIDATION_START = pd.Timestamp("2006-11-05")
VALIDATION_END = pd.Timestamp("2007-10-28")
TEST_START = pd.Timestamp("2007-11-04")
TEST_END = pd.Timestamp("2019-07-14")
GAP_START = pd.Timestamp("2009-04-05")
GAP_END = pd.Timestamp("2010-03-21")
HORIZONS = tuple(range(9))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_sha(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


def metrics(actual: np.ndarray, predicted: np.ndarray) -> dict[str, float | int]:
    actual = np.asarray(actual, dtype=float)
    predicted = np.asarray(predicted, dtype=float)
    error = predicted - actual
    absolute = np.abs(error)
    denominator = float(np.sum(np.abs(actual)))
    wape = float(np.sum(absolute) / denominator) if denominator > 0 else math.nan
    return {
        "n": int(len(actual)),
        "wape": wape,
        "agreement": max(0.0, 1.0 - wape),
        "rmse": float(np.sqrt(np.mean(error**2))),
        "mae": float(np.mean(absolute)),
    }


@dataclass(frozen=True)
class SeriesData:
    frame: pd.DataFrame
    dates: pd.DatetimeIndex
    values: np.ndarray
    date_to_index: dict[pd.Timestamp, int]


def load_series(data_zip: Path) -> SeriesData:
    with zipfile.ZipFile(data_zip) as archive:
        payload = archive.read("Data/data_no_absent.csv")
    frame = pd.read_csv(io.BytesIO(payload), usecols=["region", "date", "rate"])
    frame = frame.loc[frame["region"] == "HK", ["date", "rate"]].copy()
    frame["date"] = pd.to_datetime(frame["date"])
    frame = frame.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
    expected = pd.date_range(frame["date"].min(), frame["date"].max(), freq="7D")
    if not frame["date"].equals(pd.Series(expected, name="date")):
        raise RuntimeError("official source series is not a complete seven-day sequence")
    if frame["rate"].isna().any() or (frame["rate"] < 0).any():
        raise RuntimeError("official source series contains invalid target values")
    dates = pd.DatetimeIndex(frame["date"])
    return SeriesData(
        frame=frame,
        dates=dates,
        values=frame["rate"].to_numpy(dtype=float),
        date_to_index={pd.Timestamp(value): index for index, value in enumerate(dates)},
    )


def load_evaluation_panel(panel_path: Path, phase: str) -> pd.DataFrame:
    usecols = ["origin", "date", "week_ahead", "true", "AWBE", "Baseline"]
    panel = pd.read_csv(panel_path, usecols=usecols)
    panel["origin"] = pd.to_datetime(panel["origin"])
    panel["date"] = pd.to_datetime(panel["date"])
    if phase == "validation":
        selected = panel[
            panel["origin"].between(VALIDATION_START, VALIDATION_END)
            & (panel["date"] <= VALIDATION_END + pd.Timedelta(weeks=8))
        ].copy()
    elif phase == "test":
        selected = panel[
            panel["origin"].between(TEST_START, TEST_END)
            & (panel["date"] <= TEST_END)
            & ~panel["origin"].between(GAP_START, GAP_END)
        ].copy()
    else:
        raise ValueError(phase)
    return selected.sort_values(["origin", "week_ahead", "date"]).reset_index(drop=True)


LAGS = (1, 2, 3, 4, 5, 6, 8, 10, 12, 13, 26, 39, 52, 53, 78, 104, 156)
ROLLING_WINDOWS = (4, 8, 13, 26, 52)


def feature_row(series: SeriesData, origin_index: int, horizon: int) -> np.ndarray | None:
    y = series.values
    target_index = origin_index + horizon
    if origin_index < 157 or target_index >= len(y):
        return None
    values: list[float] = []
    for lag in LAGS:
        values.append(float(y[origin_index - lag]))
    for window in ROLLING_WINDOWS:
        block = y[origin_index - window : origin_index]
        values.extend(
            [
                float(np.mean(block)),
                float(np.std(block)),
                float(np.min(block)),
                float(np.max(block)),
            ]
        )
    recent = y[origin_index - 4 : origin_index]
    previous = y[origin_index - 8 : origin_index - 4]
    values.extend(
        [
            float(np.mean(recent) - np.mean(previous)),
            float(y[origin_index - 1] - y[origin_index - 2]),
            float(y[origin_index - 1] / max(y[origin_index - 2], 0.05)),
        ]
    )
    seasonal_values = [float(y[target_index - lag]) for lag in (52, 104, 156)]
    values.extend(seasonal_values)
    values.extend([float(np.mean(seasonal_values)), float(np.median(seasonal_values))])
    target_week = int(series.dates[target_index].isocalendar().week)
    origin_week = int(series.dates[origin_index].isocalendar().week)
    values.extend(
        [
            float(horizon),
            math.sin(2 * math.pi * target_week / 52.1775),
            math.cos(2 * math.pi * target_week / 52.1775),
            math.sin(4 * math.pi * target_week / 52.1775),
            math.cos(4 * math.pi * target_week / 52.1775),
            math.sin(2 * math.pi * origin_week / 52.1775),
            math.cos(2 * math.pi * origin_week / 52.1775),
        ]
    )
    return np.asarray(values, dtype=float)


def training_matrix(series: SeriesData, cutoff_index: int) -> tuple[np.ndarray, np.ndarray]:
    rows: list[np.ndarray] = []
    labels: list[float] = []
    for origin_index in range(157, cutoff_index):
        for horizon in HORIZONS:
            target_index = origin_index + horizon
            if target_index >= cutoff_index:
                continue
            feature = feature_row(series, origin_index, horizon)
            if feature is None:
                continue
            rows.append(feature)
            labels.append(float(series.values[target_index]))
    if not rows:
        raise RuntimeError("empty training matrix")
    return np.vstack(rows), np.asarray(labels, dtype=float)


def model_factories() -> dict[str, object]:
    return {
        "ridge_log_a1": make_pipeline(StandardScaler(), Ridge(alpha=1.0)),
        "ridge_log_a10": make_pipeline(StandardScaler(), Ridge(alpha=10.0)),
        "ridge_log_a100": make_pipeline(StandardScaler(), Ridge(alpha=100.0)),
        "hist_poisson_d3": HistGradientBoostingRegressor(
            loss="poisson", learning_rate=0.06, max_iter=180, max_leaf_nodes=15,
            l2_regularization=2.0, random_state=SEED,
        ),
        "hist_poisson_d5": HistGradientBoostingRegressor(
            loss="poisson", learning_rate=0.04, max_iter=220, max_leaf_nodes=31,
            l2_regularization=4.0, random_state=SEED,
        ),
        "gbr_huber": GradientBoostingRegressor(
            loss="huber", learning_rate=0.03, n_estimators=220, max_depth=2,
            min_samples_leaf=8, random_state=SEED,
        ),
        "extra_trees": ExtraTreesRegressor(
            n_estimators=160, max_features=0.8, min_samples_leaf=3,
            max_depth=14, n_jobs=1, random_state=SEED,
        ),
        "random_forest": RandomForestRegressor(
            n_estimators=160, max_features=0.8, min_samples_leaf=4,
            max_depth=14, n_jobs=1, random_state=SEED,
        ),
    }


def predict_simple(series: SeriesData, origin_index: int, horizon: int) -> dict[str, float]:
    y = series.values
    target_index = origin_index + horizon
    seasonal = [float(y[target_index - lag]) for lag in (52, 104, 156)]
    recent_mean = float(np.mean(y[origin_index - 4 : origin_index]))
    prior_year_mean = float(np.mean(y[origin_index - 56 : origin_index - 52]))
    ratio = float(np.clip(recent_mean / max(prior_year_mean, 0.05), 0.5, 2.0))
    additive = recent_mean - prior_year_mean
    return {
        "last_value": float(y[origin_index - 1]),
        "seasonal_52": seasonal[0],
        "seasonal_mean_2": float(np.mean(seasonal[:2])),
        "seasonal_mean_3": float(np.mean(seasonal)),
        "seasonal_median_3": float(np.median(seasonal)),
        "seasonal_ratio": max(0.0, seasonal[0] * ratio),
        "seasonal_additive": max(0.0, seasonal[0] + additive),
    }


def analog_prediction(
    series: SeriesData,
    origin_index: int,
    horizon: int,
    window: int,
    neighbors: int,
    calendar_penalty: float,
) -> float:
    y = series.values
    query = y[origin_index - window : origin_index]
    q_scale = max(float(np.mean(query)), 0.05)
    q_shape = query / q_scale
    candidates: list[tuple[float, float]] = []
    origin_week = int(series.dates[origin_index].isocalendar().week)
    for historical_origin in range(max(157, window), origin_index - horizon):
        if historical_origin + horizon >= origin_index:
            continue
        block = y[historical_origin - window : historical_origin]
        scale = max(float(np.mean(block)), 0.05)
        shape_distance = float(np.sqrt(np.mean((q_shape - block / scale) ** 2)))
        level_distance = abs(math.log(q_scale / scale))
        historical_week = int(series.dates[historical_origin].isocalendar().week)
        week_distance = min(abs(origin_week - historical_week), 52 - abs(origin_week - historical_week)) / 26.0
        distance = shape_distance + 0.35 * level_distance + calendar_penalty * week_distance
        future = float(y[historical_origin + horizon]) * q_scale / scale
        candidates.append((distance, max(0.0, future)))
    if not candidates:
        return float(y[origin_index - 1])
    chosen = sorted(candidates, key=lambda item: item[0])[:neighbors]
    distances = np.asarray([item[0] for item in chosen], dtype=float)
    forecasts = np.asarray([item[1] for item in chosen], dtype=float)
    bandwidth = max(float(np.median(distances)), 1e-6)
    weights = np.exp(-distances / bandwidth)
    return float(np.dot(weights, forecasts) / weights.sum())


ANALOG_SPECS = {
    "analog_w4_k8_c05": (4, 8, 0.5),
    "analog_w8_k8_c05": (8, 8, 0.5),
    "analog_w8_k16_c05": (8, 16, 0.5),
    "analog_w13_k8_c10": (13, 8, 1.0),
    "analog_w13_k16_c10": (13, 16, 1.0),
}


def predict_evaluation(series: SeriesData, evaluation: pd.DataFrame) -> pd.DataFrame:
    records: list[dict[str, object]] = []
    factories = model_factories()
    for origin, origin_rows in evaluation.groupby("origin", sort=True):
        origin = pd.Timestamp(origin)
        origin_index = series.date_to_index.get(origin)
        if origin_index is None:
            raise RuntimeError(f"origin missing from official series: {origin}")
        x_train, y_train = training_matrix(series, origin_index)
        trained: dict[str, object] = {}
        for name, model in factories.items():
            if name.startswith("ridge_log"):
                model.fit(x_train, np.log1p(y_train))
            else:
                model.fit(x_train, y_train)
            trained[name] = model
        for row in origin_rows.itertuples(index=False):
            horizon = int(row.week_ahead)
            feature = feature_row(series, origin_index, horizon)
            if feature is None:
                raise RuntimeError("evaluation feature unavailable")
            result: dict[str, object] = {
                "origin": origin,
                "date": pd.Timestamp(row.date),
                "week_ahead": horizon,
                "true": float(row.true),
                "AWBE": float(row.AWBE),
                "Baseline": float(row.Baseline),
            }
            if abs(float(row.true) - float(series.values[origin_index + horizon])) > 1e-9:
                raise RuntimeError("evaluation label does not match official source series")
            result.update(predict_simple(series, origin_index, horizon))
            for name, model in trained.items():
                predicted = float(model.predict(feature.reshape(1, -1))[0])
                if name.startswith("ridge_log"):
                    predicted = math.expm1(predicted)
                result[name] = max(0.0, predicted)
            for name, (window, neighbors, penalty) in ANALOG_SPECS.items():
                result[name] = analog_prediction(
                    series, origin_index, horizon, window, neighbors, penalty
                )
            records.append(result)
    return pd.DataFrame.from_records(records).sort_values(
        ["origin", "week_ahead", "date"]
    ).reset_index(drop=True)


def model_columns(predictions: pd.DataFrame) -> list[str]:
    excluded = {"origin", "date", "week_ahead", "true", "AWBE", "Baseline"}
    return [column for column in predictions.columns if column not in excluded]


def metric_tables(predictions: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    overall: list[dict[str, object]] = []
    by_horizon: list[dict[str, object]] = []
    for model in ["AWBE", "Baseline", *model_columns(predictions)]:
        overall.append(
            {"model": model, **metrics(predictions["true"], predictions[model])}
        )
        for horizon, part in predictions.groupby("week_ahead"):
            by_horizon.append(
                {
                    "model": model,
                    "week_ahead": int(horizon),
                    **metrics(part["true"], part[model]),
                }
            )
    return (
        pd.DataFrame(overall).sort_values(["wape", "rmse", "model"]),
        pd.DataFrame(by_horizon).sort_values(["week_ahead", "wape", "rmse", "model"]),
    )


def build_route_lock(
    predictions: pd.DataFrame,
    data_zip: Path,
    panel_path: Path,
    by_horizon: pd.DataFrame,
) -> dict[str, object]:
    routes: dict[str, object] = {}
    project_models = set(model_columns(predictions))
    for horizon in HORIZONS:
        part = by_horizon[
            (by_horizon["week_ahead"] == horizon)
            & by_horizon["model"].isin(project_models)
        ].copy()
        ranked = part.sort_values(["wape", "rmse", "model"]).reset_index(drop=True)
        selected = ranked.iloc[0]
        routes[str(horizon)] = {
            "model": str(selected["model"]),
            "validation_wape": float(selected["wape"]),
            "validation_rmse": float(selected["rmse"]),
            "validation_n": int(selected["n"]),
            "top_five": ranked.head(5)[["model", "wape", "rmse", "n"]].to_dict("records"),
        }
    lock: dict[str, object] = {
        "status": "LOCKED_BEFORE_TEST",
        "model_identity": "PROJECT_INDEPENDENT_HORIZON_ROUTED_UNIVARIATE_V1",
        "paper_component_predictions_used_as_project_features": False,
        "paper_methods_used_as_project_candidates": False,
        "source_features": "official weekly influenza rate history only",
        "validation_origin_start": VALIDATION_START.date().isoformat(),
        "validation_origin_end": VALIDATION_END.date().isoformat(),
        "selection_metric": "per-horizon WAPE; RMSE then lexical model ID tie-break",
        "data_zip_sha256": sha256_file(data_zip),
        "evaluation_panel_sha256": sha256_file(panel_path),
        "routes": routes,
    }
    lock["lock_sha256"] = canonical_sha(lock)
    return lock


def apply_route(predictions: pd.DataFrame, lock: dict[str, object]) -> pd.DataFrame:
    output = predictions.copy()
    values: list[float] = []
    models: list[str] = []
    for row in output.itertuples(index=False):
        model = str(lock["routes"][str(int(row.week_ahead))]["model"])
        models.append(model)
        values.append(float(getattr(row, model)))
    output["PROJECT_INDEPENDENT_ROUTE"] = values
    output["project_route_model"] = models
    return output


def verify_future_sentinel(
    series: SeriesData,
    evaluation: pd.DataFrame,
    prediction: pd.DataFrame,
) -> bool:
    cutoff = pd.Timestamp(evaluation["origin"].sort_values().iloc[len(evaluation) // 2])
    eligible = evaluation[evaluation["date"] < cutoff].copy()
    before = prediction[prediction["date"] < cutoff].copy()
    mutated_frame = series.frame.copy()
    mutated_frame.loc[mutated_frame["date"] >= cutoff, "rate"] += 9999.0
    mutated = SeriesData(
        frame=mutated_frame,
        dates=series.dates,
        values=mutated_frame["rate"].to_numpy(dtype=float),
        date_to_index=series.date_to_index,
    )
    after = predict_evaluation(mutated, eligible)
    columns = model_columns(prediction)
    return np.array_equal(before[columns].to_numpy(), after[columns].to_numpy())


def write_manifest(output_dir: Path) -> None:
    entries = []
    for path in sorted(output_dir.iterdir()):
        if path.is_file() and path.name != "MANIFEST.sha256":
            entries.append(f"{sha256_file(path)}  {path.name}")
    (output_dir / "MANIFEST.sha256").write_text("\n".join(entries) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=["validation", "test"], required=True)
    parser.add_argument("--data-zip", type=Path, required=True)
    parser.add_argument("--panel", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--lock", type=Path)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise RuntimeError(f"output directory already exists: {args.output_dir}")
    args.output_dir.mkdir(parents=True)

    series = load_series(args.data_zip)
    evaluation = load_evaluation_panel(args.panel, args.phase)
    predictions = predict_evaluation(series, evaluation)
    overall, by_horizon = metric_tables(predictions)
    predictions.to_csv(args.output_dir / f"{args.phase}_candidate_predictions.csv", index=False)
    overall.to_csv(args.output_dir / f"{args.phase}_metrics_overall.csv", index=False)
    by_horizon.to_csv(args.output_dir / f"{args.phase}_metrics_by_horizon.csv", index=False)

    if args.phase == "validation":
        lock = build_route_lock(predictions, args.data_zip, args.panel, by_horizon)
        write_json(args.output_dir / "route_lock.json", lock)
        routed = apply_route(predictions, lock)
        routed.to_csv(args.output_dir / "validation_routed_predictions.csv", index=False)
        summary = {
            "status": "VALIDATION_COMPLETE_ROUTE_LOCKED",
            "route_lock_sha256": lock["lock_sha256"],
            "project": metrics(routed["true"], routed["PROJECT_INDEPENDENT_ROUTE"]),
            "paper_awbe_reference_only": metrics(routed["true"], routed["AWBE"]),
            "paper_methods_used_by_project": False,
            "test_labels_opened": False,
        }
    else:
        if args.lock is None:
            raise RuntimeError("--lock is required for test phase")
        lock = json.loads(args.lock.read_text(encoding="utf-8"))
        expected = str(lock.pop("lock_sha256"))
        if canonical_sha(lock) != expected:
            raise RuntimeError("route lock SHA mismatch")
        lock["lock_sha256"] = expected
        if sha256_file(args.data_zip) != lock["data_zip_sha256"]:
            raise RuntimeError("source data SHA changed after route lock")
        if sha256_file(args.panel) != lock["evaluation_panel_sha256"]:
            raise RuntimeError("evaluation panel SHA changed after route lock")
        routed = apply_route(predictions, lock)
        routed.to_csv(args.output_dir / "test_routed_predictions.csv", index=False)
        replay = predict_evaluation(series, evaluation)
        deterministic = np.array_equal(
            predictions[model_columns(predictions)].to_numpy(),
            replay[model_columns(replay)].to_numpy(),
        )
        sentinel = verify_future_sentinel(series, evaluation, predictions)
        project_metric = metrics(routed["true"], routed["PROJECT_INDEPENDENT_ROUTE"])
        paper_metric = metrics(routed["true"], routed["AWBE"])
        summary = {
            "status": "INDEPENDENT_PROJECT_TEST_COMPLETE",
            "model_identity": lock["model_identity"],
            "route_lock_sha256": expected,
            "project": project_metric,
            "paper_awbe_reference_only": paper_metric,
            "absolute_wape_gap": project_metric["wape"] - paper_metric["wape"],
            "relative_wape_gap": project_metric["wape"] / paper_metric["wape"] - 1.0,
            "project_within_five_wape_points": project_metric["wape"] <= paper_metric["wape"] + 0.05,
            "paper_methods_used_by_project": False,
            "paper_component_predictions_used_by_project": False,
            "deterministic_replay": deterministic,
            "future_label_sentinel": sentinel,
            "database_writes": 0,
            "forecast_runs_created": 0,
            "promotion_changes": 0,
            "active_pointer_changes": 0,
        }
        if not deterministic or not sentinel:
            summary["status"] = "INDEPENDENT_PROJECT_TEST_FAILED_SAFETY_GATE"
    write_json(args.output_dir / "summary.json", summary)
    write_manifest(args.output_dir)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
