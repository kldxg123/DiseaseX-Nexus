from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesRegressor, HistGradientBoostingRegressor
from sklearn.linear_model import HuberRegressor, Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler


SEED = 20260825
LAGS = (1, 2, 3, 4, 5, 6, 8, 10, 12, 13, 26, 39, 52, 53, 78, 104, 156)
WINDOWS = (4, 8, 13, 26, 52)


def load_module(path: Path):
    spec = importlib.util.spec_from_file_location("independent_benchmark", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load benchmark module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def one_step_features(values: np.ndarray, dates: pd.DatetimeIndex, target_index: int) -> np.ndarray:
    if target_index < 157:
        raise RuntimeError("insufficient history")
    features: list[float] = [float(values[target_index - lag]) for lag in LAGS]
    for window in WINDOWS:
        block = values[target_index - window : target_index]
        features.extend(
            [float(np.mean(block)), float(np.std(block)), float(np.min(block)), float(np.max(block))]
        )
    recent = values[target_index - 4 : target_index]
    previous = values[target_index - 8 : target_index - 4]
    features.extend(
        [
            float(np.mean(recent) - np.mean(previous)),
            float(values[target_index - 1] - values[target_index - 2]),
            float(values[target_index - 1] / max(values[target_index - 2], 0.05)),
        ]
    )
    week = int(dates[target_index].isocalendar().week)
    features.extend(
        [
            math.sin(2 * math.pi * week / 52.1775),
            math.cos(2 * math.pi * week / 52.1775),
            math.sin(4 * math.pi * week / 52.1775),
            math.cos(4 * math.pi * week / 52.1775),
            math.sin(6 * math.pi * week / 52.1775),
            math.cos(6 * math.pi * week / 52.1775),
        ]
    )
    return np.asarray(features, dtype=float)


def training_matrix(series, cutoff_index: int) -> tuple[np.ndarray, np.ndarray]:
    rows = [one_step_features(series.values, series.dates, index) for index in range(157, cutoff_index)]
    labels = series.values[157:cutoff_index].astype(float)
    return np.vstack(rows), labels


def factories() -> dict[str, object]:
    return {
        "recursive_ridge_raw_a01": make_pipeline(StandardScaler(), Ridge(alpha=0.1)),
        "recursive_ridge_raw_a1": make_pipeline(StandardScaler(), Ridge(alpha=1.0)),
        "recursive_ridge_raw_a10": make_pipeline(StandardScaler(), Ridge(alpha=10.0)),
        "recursive_ridge_raw_a100": make_pipeline(StandardScaler(), Ridge(alpha=100.0)),
        "recursive_ridge_log_a1": make_pipeline(StandardScaler(), Ridge(alpha=1.0)),
        "recursive_ridge_log_a10": make_pipeline(StandardScaler(), Ridge(alpha=10.0)),
        "recursive_huber": make_pipeline(
            StandardScaler(), HuberRegressor(epsilon=1.5, alpha=0.001, max_iter=500)
        ),
        "recursive_hist_poisson": HistGradientBoostingRegressor(
            loss="poisson", learning_rate=0.05, max_iter=180, max_leaf_nodes=15,
            min_samples_leaf=12, l2_regularization=3.0, random_state=SEED,
        ),
        "recursive_extra_trees": ExtraTreesRegressor(
            n_estimators=180, max_features=0.8, min_samples_leaf=3,
            max_depth=16, n_jobs=1, random_state=SEED,
        ),
    }


def recursive_forecast(model, model_name: str, series, origin_index: int) -> list[float]:
    working = np.concatenate([series.values[:origin_index].copy(), np.zeros(9, dtype=float)])
    dates = series.dates.append(
        pd.date_range(series.dates[-1] + pd.Timedelta(days=7), periods=9, freq="7D")
    ) if origin_index + 8 >= len(series.dates) else series.dates
    forecasts: list[float] = []
    for step in range(9):
        target_index = origin_index + step
        feature = one_step_features(working, dates, target_index)
        predicted = float(model.predict(feature.reshape(1, -1))[0])
        if "_log_" in model_name:
            upper_log = math.log1p(max(float(np.max(series.values[:origin_index])) * 3.0, 1.0))
            predicted = math.expm1(float(np.clip(predicted, 0.0, upper_log)))
        predicted = float(
            np.clip(predicted, 0.0, max(float(np.max(series.values[:origin_index])) * 3.0, 1.0))
        )
        working[target_index] = predicted
        forecasts.append(predicted)
    return forecasts


def predict_candidates(module, series, evaluation: pd.DataFrame, selected: set[str] | None = None) -> pd.DataFrame:
    all_factories = factories()
    if selected is not None:
        unknown = selected - set(all_factories)
        if unknown:
            raise RuntimeError(f"unknown locked recursive model: {sorted(unknown)}")
        all_factories = {name: model for name, model in all_factories.items() if name in selected}
    records: list[dict[str, object]] = []
    for origin, rows in evaluation.groupby("origin", sort=True):
        origin = pd.Timestamp(origin)
        origin_index = series.date_to_index[origin]
        x_train, y_train = training_matrix(series, origin_index)
        forecasts: dict[str, list[float]] = {}
        for name, model in all_factories.items():
            target = np.log1p(y_train) if "_log_" in name else y_train
            model.fit(x_train, target)
            forecasts[name] = recursive_forecast(model, name, series, origin_index)
        for row in rows.itertuples(index=False):
            horizon = int(row.week_ahead)
            if abs(float(row.true) - float(series.values[origin_index + horizon])) > 1e-9:
                raise RuntimeError("label/source mismatch")
            record: dict[str, object] = {
                "origin": origin,
                "date": pd.Timestamp(row.date),
                "week_ahead": horizon,
                "true": float(row.true),
                "AWBE": float(row.AWBE),
                "Baseline": float(row.Baseline),
            }
            for name, values in forecasts.items():
                record[name] = values[horizon]
            records.append(record)
    return pd.DataFrame(records).sort_values(["origin", "week_ahead", "date"]).reset_index(drop=True)


def metric_tables(module, predictions: pd.DataFrame, models: list[str]):
    overall: list[dict[str, object]] = []
    horizon: list[dict[str, object]] = []
    for model in ["AWBE", "Baseline", *models]:
        overall.append({"model": model, **module.metrics(predictions["true"], predictions[model])})
        for value, part in predictions.groupby("week_ahead"):
            horizon.append(
                {"model": model, "week_ahead": int(value), **module.metrics(part["true"], part[model])}
            )
    return (
        pd.DataFrame(overall).sort_values(["wape", "rmse", "model"]),
        pd.DataFrame(horizon).sort_values(["week_ahead", "wape", "rmse", "model"]),
    )


def build_lock(module, predictions: pd.DataFrame, by_horizon: pd.DataFrame, data_zip: Path, panel: Path):
    models = list(factories())
    routes: dict[str, object] = {}
    for value in module.HORIZONS:
        ranked = by_horizon[
            (by_horizon["week_ahead"] == value) & by_horizon["model"].isin(models)
        ].sort_values(["wape", "rmse", "model"]).reset_index(drop=True)
        selected = ranked.iloc[0]
        routes[str(value)] = {
            "model": str(selected["model"]),
            "validation_wape": float(selected["wape"]),
            "validation_rmse": float(selected["rmse"]),
            "validation_n": int(selected["n"]),
            "ranking": ranked[["model", "wape", "rmse", "n"]].to_dict("records"),
        }
    lock: dict[str, object] = {
        "status": "LOCKED_BEFORE_RECURSIVE_TEST",
        "model_identity": "PROJECT_RECURSIVE_SEASONAL_AUTOREGRESSION_V2",
        "paper_methods_used_as_project_candidates": False,
        "paper_component_predictions_used_as_project_features": False,
        "source_features": "official weekly influenza rate history only",
        "validation_origin_start": module.VALIDATION_START.date().isoformat(),
        "validation_origin_end": module.VALIDATION_END.date().isoformat(),
        "selection_metric": "per-horizon validation WAPE",
        "data_zip_sha256": module.sha256_file(data_zip),
        "evaluation_panel_sha256": module.sha256_file(panel),
        "routes": routes,
    }
    lock["lock_sha256"] = module.canonical_sha(lock)
    return lock


def routed(module, predictions: pd.DataFrame, lock: dict) -> pd.DataFrame:
    output = predictions.copy()
    models: list[str] = []
    values: list[float] = []
    for row in output.itertuples(index=False):
        model = str(lock["routes"][str(int(row.week_ahead))]["model"])
        models.append(model)
        values.append(float(getattr(row, model)))
    output["project_route_model"] = models
    output["PROJECT_RECURSIVE_ROUTE"] = values
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=["validation", "test"], required=True)
    parser.add_argument("--module", type=Path, required=True)
    parser.add_argument("--data-zip", type=Path, required=True)
    parser.add_argument("--panel", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--lock", type=Path)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise RuntimeError(f"output directory already exists: {args.output_dir}")
    args.output_dir.mkdir(parents=True)
    module = load_module(args.module.resolve())
    series = module.load_series(args.data_zip)
    evaluation = module.load_evaluation_panel(args.panel, args.phase)

    if args.phase == "validation":
        predictions = predict_candidates(module, series, evaluation)
        overall, by_horizon = metric_tables(module, predictions, list(factories()))
        lock = build_lock(module, predictions, by_horizon, args.data_zip, args.panel)
        module.write_json(args.output_dir / "route_lock.json", lock)
    else:
        if args.lock is None:
            raise RuntimeError("test requires --lock")
        lock = json.loads(args.lock.read_text(encoding="utf-8"))
        expected = str(lock.pop("lock_sha256"))
        if module.canonical_sha(lock) != expected:
            raise RuntimeError("lock SHA mismatch")
        lock["lock_sha256"] = expected
        if module.sha256_file(args.data_zip) != lock["data_zip_sha256"]:
            raise RuntimeError("data SHA mismatch")
        if module.sha256_file(args.panel) != lock["evaluation_panel_sha256"]:
            raise RuntimeError("panel SHA mismatch")
        selected = {str(value["model"]) for value in lock["routes"].values()}
        predictions = predict_candidates(module, series, evaluation, selected)
        overall, by_horizon = metric_tables(module, predictions, sorted(selected))

    result = routed(module, predictions, lock)
    result.to_csv(args.output_dir / f"{args.phase}_routed_predictions.csv", index=False)
    overall.to_csv(args.output_dir / f"{args.phase}_metrics_overall.csv", index=False)
    by_horizon.to_csv(args.output_dir / f"{args.phase}_metrics_by_horizon.csv", index=False)
    project = module.metrics(result["true"], result["PROJECT_RECURSIVE_ROUTE"])
    awbe = module.metrics(result["true"], result["AWBE"])
    summary = {
        "status": f"RECURSIVE_{args.phase.upper()}_COMPLETE",
        "model_identity": lock["model_identity"],
        "route_lock_sha256": lock["lock_sha256"],
        "project": project,
        "paper_awbe_reference_only": awbe,
        "absolute_wape_gap": float(project["wape"] - awbe["wape"]),
        "paper_methods_used_by_project": False,
        "paper_component_predictions_used_by_project": False,
        "test_labels_opened": args.phase == "test",
        "database_writes": 0,
        "forecast_runs_created": 0,
        "promotion_changes": 0,
        "active_pointer_changes": 0,
    }
    module.write_json(args.output_dir / "summary.json", summary)
    module.write_manifest(args.output_dir)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
