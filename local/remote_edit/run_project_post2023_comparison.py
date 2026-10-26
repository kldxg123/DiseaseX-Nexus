from __future__ import annotations

import csv
import hashlib
import itertools
import json
import math
import sys
from datetime import date, timedelta
from pathlib import Path

from epidemic_core.kg_qwen.adapters_ext import (
    fit_lagged_count_boosting,
    predict_lagged_count_boosting,
)
from epidemic_core.kg_r3c.execution import TrainingRow


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def metrics(actual: list[float], prediction: list[float], scale_source: list[float]) -> dict[str, float]:
    abs_errors = [abs(a - p) for a, p in zip(actual, prediction, strict=True)]
    denom = sum(abs(a) for a in actual)
    wape = sum(abs_errors) / denom if denom else math.nan
    mae = sum(abs_errors) / len(abs_errors)
    rmse = math.sqrt(sum((a - p) ** 2 for a, p in zip(actual, prediction, strict=True)) / len(actual))
    smape = sum(
        0.0 if abs(a) + abs(p) == 0 else 2.0 * abs(a - p) / (abs(a) + abs(p))
        for a, p in zip(actual, prediction, strict=True)
    ) / len(actual)
    train_scale = sum(abs(b - a) for a, b in zip(scale_source, scale_source[1:], strict=False)) / (len(scale_source) - 1)
    paper_scale = sum(abs(b - a) for a, b in zip(actual, actual[1:], strict=False)) / (len(actual) - 1)
    return {
        "wape": wape,
        "agreement": 1.0 - wape,
        "mae": mae,
        "rmse": rmse,
        "smape": smape,
        "paper_metrics_mase": mae / paper_scale,
        "standard_train_scaled_mase": mae / train_scale,
    }


def training_rows(values: list[float]) -> list[TrainingRow]:
    start = date(2000, 1, 3)
    return [
        TrainingRow(period_start=start + timedelta(weeks=i), period_end=None, value=value, observation_id=f"paper-{i+1}")
        for i, value in enumerate(values)
    ]


def forecast(values: list[float], params: dict[str, float | int], horizon: int) -> list[float]:
    rows = training_rows(values)
    cutoff = rows[-1].period_start
    fitted = fit_lagged_count_boosting(rows, params, 20260813, cutoff)
    output = predict_lagged_count_boosting(fitted, list(range(1, horizon + 1)), cutoff)
    predictions = [float(item["yhat"]) for item in output]
    if len(predictions) != horizon:
        raise RuntimeError("project adapter produced incomplete output")
    return predictions


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: runner DATA_CSV OUT_DIR")
    source = Path(sys.argv[1])
    out = Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    with source.open(newline="", encoding="utf-8") as handle:
        raw = list(csv.DictReader(handle))
    values = [float(row["cases"]) for row in raw]
    if len(values) != 838:
        raise RuntimeError("unexpected input row count")
    horizon, n_val = 13, 26
    train = values[: -(horizon + n_val)]
    validation = values[-(horizon + n_val) : -horizon]
    train_validation = values[:-horizon]
    test = values[-horizon:]

    grid = itertools.product((30, 50, 80, 100), (0.03, 0.05, 0.1, 0.2), (5, 10, 15, 20))
    grid_rows: list[dict[str, float | int]] = []
    for n_estimators, learning_rate, min_samples_leaf in grid:
        params = {
            "n_estimators": n_estimators,
            "learning_rate": learning_rate,
            "min_samples_leaf": min_samples_leaf,
        }
        pred = forecast(train, params, n_val)
        row = {**params, **metrics(validation, pred, train)}
        grid_rows.append(row)
    grid_rows.sort(key=lambda row: (float(row["paper_metrics_mase"]), float(row["wape"]), int(row["n_estimators"]), float(row["learning_rate"]), int(row["min_samples_leaf"])))
    best = grid_rows[0]
    best_params = {key: best[key] for key in ("n_estimators", "learning_rate", "min_samples_leaf")}
    project_prediction = forecast(train_validation, best_params, horizon)

    baseline_predictions = {
        "last_value": [train_validation[-1]] * horizon,
        "rolling_mean_4": [sum(train_validation[-4:]) / 4.0] * horizon,
        "seasonal_naive_52": values[-(horizon + 52) : -52],
    }
    results = {
        "selection_protocol": "64-point grid; validation-only MASE minimization; test opened once after parameter selection",
        "dataset_sha256": sha256(source),
        "n_total": len(values),
        "n_train": len(train),
        "n_validation": len(validation),
        "n_test": len(test),
        "best_parameters": best_params,
        "validation_metrics": {key: best[key] for key in ("wape", "agreement", "mae", "rmse", "smape", "paper_metrics_mase", "standard_train_scaled_mase")},
        "test_metrics": {"lagged_count_boosting.v1": metrics(test, project_prediction, train_validation)},
    }
    for name, pred in baseline_predictions.items():
        results["test_metrics"][name] = metrics(test, pred, train_validation)

    (out / "project_metrics.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    with (out / "project_grid_search.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(grid_rows[0]))
        writer.writeheader()
        writer.writerows(grid_rows)
    with (out / "project_predictions.csv").open("w", newline="", encoding="utf-8") as handle:
        fieldnames = ["row_index", "year", "week", "actual", "lagged_count_boosting.v1", *baseline_predictions]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for i in range(horizon):
            source_row = raw[-horizon + i]
            writer.writerow(
                {
                    "row_index": len(values) - horizon + i + 1,
                    "year": source_row["year"],
                    "week": source_row["week"],
                    "actual": test[i],
                    "lagged_count_boosting.v1": project_prediction[i],
                    **{name: prediction[i] for name, prediction in baseline_predictions.items()},
                }
            )
    print(json.dumps(results, ensure_ascii=False))


if __name__ == "__main__":
    main()
