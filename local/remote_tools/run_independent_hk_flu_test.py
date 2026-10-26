from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd


def load_module(path: Path):
    spec = importlib.util.spec_from_file_location("independent_benchmark", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load benchmark module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def selected_predictions(module, series, evaluation: pd.DataFrame, lock: dict) -> pd.DataFrame:
    selected = {str(route["model"]) for route in lock["routes"].values()}
    allowed = set(module.ANALOG_SPECS) | {
        "last_value",
        "seasonal_52",
        "seasonal_mean_2",
        "seasonal_mean_3",
        "seasonal_median_3",
        "seasonal_ratio",
        "seasonal_additive",
    }
    unsupported = selected - allowed
    if unsupported:
        raise RuntimeError(f"locked route requires unsupported fast-path models: {sorted(unsupported)}")

    records: list[dict[str, object]] = []
    for row in evaluation.itertuples(index=False):
        origin = pd.Timestamp(row.origin)
        horizon = int(row.week_ahead)
        origin_index = series.date_to_index[origin]
        if abs(float(row.true) - float(series.values[origin_index + horizon])) > 1e-9:
            raise RuntimeError("evaluation label does not match official source series")
        model = str(lock["routes"][str(horizon)]["model"])
        if model in module.ANALOG_SPECS:
            window, neighbors, penalty = module.ANALOG_SPECS[model]
            prediction = module.analog_prediction(
                series, origin_index, horizon, window, neighbors, penalty
            )
        else:
            prediction = module.predict_simple(series, origin_index, horizon)[model]
        records.append(
            {
                "origin": origin,
                "date": pd.Timestamp(row.date),
                "week_ahead": horizon,
                "true": float(row.true),
                "AWBE": float(row.AWBE),
                "Baseline": float(row.Baseline),
                "project_route_model": model,
                "PROJECT_INDEPENDENT_ROUTE": max(0.0, float(prediction)),
            }
        )
    return pd.DataFrame.from_records(records).sort_values(
        ["origin", "week_ahead", "date"]
    ).reset_index(drop=True)


def horizon_metrics(module, predictions: pd.DataFrame) -> pd.DataFrame:
    records: list[dict[str, object]] = []
    for horizon, part in predictions.groupby("week_ahead"):
        for model, column in (
            ("PROJECT_INDEPENDENT_ROUTE", "PROJECT_INDEPENDENT_ROUTE"),
            ("AWBE_REFERENCE_ONLY", "AWBE"),
            ("PAPER_BASELINE_REFERENCE_ONLY", "Baseline"),
        ):
            records.append(
                {
                    "model": model,
                    "week_ahead": int(horizon),
                    **module.metrics(part["true"], part[column]),
                }
            )
    return pd.DataFrame(records).sort_values(["week_ahead", "wape", "model"])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--module", type=Path, required=True)
    parser.add_argument("--data-zip", type=Path, required=True)
    parser.add_argument("--panel", type=Path, required=True)
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise RuntimeError(f"output directory already exists: {args.output_dir}")
    args.output_dir.mkdir(parents=True)

    module = load_module(args.module.resolve())
    lock = json.loads(args.lock.read_text(encoding="utf-8"))
    lock_sha = str(lock.pop("lock_sha256"))
    if module.canonical_sha(lock) != lock_sha:
        raise RuntimeError("route lock SHA mismatch")
    lock["lock_sha256"] = lock_sha
    if module.sha256_file(args.data_zip) != lock["data_zip_sha256"]:
        raise RuntimeError("official data SHA mismatch")
    if module.sha256_file(args.panel) != lock["evaluation_panel_sha256"]:
        raise RuntimeError("evaluation panel SHA mismatch")

    series = module.load_series(args.data_zip)
    evaluation = module.load_evaluation_panel(args.panel, "test")
    predictions = selected_predictions(module, series, evaluation, lock)
    predictions.to_csv(args.output_dir / "test_routed_predictions.csv", index=False)
    by_horizon = horizon_metrics(module, predictions)
    by_horizon.to_csv(args.output_dir / "test_metrics_by_horizon.csv", index=False)

    replay = selected_predictions(module, series, evaluation, lock)
    deterministic = np.array_equal(
        predictions["PROJECT_INDEPENDENT_ROUTE"].to_numpy(),
        replay["PROJECT_INDEPENDENT_ROUTE"].to_numpy(),
    )

    cutoff = pd.Timestamp("2014-01-05")
    before_rows = evaluation[evaluation["date"] < cutoff].copy()
    before = predictions[predictions["date"] < cutoff].copy()
    mutated_frame = series.frame.copy()
    mutated_frame.loc[mutated_frame["date"] >= cutoff, "rate"] += 9999.0
    mutated = module.SeriesData(
        frame=mutated_frame,
        dates=series.dates,
        values=mutated_frame["rate"].to_numpy(dtype=float),
        date_to_index=series.date_to_index,
    )
    after = selected_predictions(module, mutated, before_rows, lock)
    future_sentinel = np.array_equal(
        before["PROJECT_INDEPENDENT_ROUTE"].to_numpy(),
        after["PROJECT_INDEPENDENT_ROUTE"].to_numpy(),
    )

    project = module.metrics(predictions["true"], predictions["PROJECT_INDEPENDENT_ROUTE"])
    awbe = module.metrics(predictions["true"], predictions["AWBE"])
    baseline = module.metrics(predictions["true"], predictions["Baseline"])
    summary = {
        "status": (
            "INDEPENDENT_PROJECT_WITHIN_COMPARABILITY_TARGET"
            if project["wape"] <= awbe["wape"] + 0.05 and deterministic and future_sentinel
            else "INDEPENDENT_PROJECT_EXPLICIT_PERFORMANCE_GAP"
        ),
        "model_identity": lock["model_identity"],
        "route_lock_sha256": lock_sha,
        "protocol": {
            "test_origin_start": module.TEST_START.date().isoformat(),
            "test_target_end": module.TEST_END.date().isoformat(),
            "excluded_origin_gap": [module.GAP_START.date().isoformat(), module.GAP_END.date().isoformat()],
            "horizons": list(module.HORIZONS),
            "paired_rows": int(len(predictions)),
        },
        "project": project,
        "paper_awbe_reference_only": awbe,
        "paper_baseline_reference_only": baseline,
        "absolute_wape_gap": float(project["wape"] - awbe["wape"]),
        "relative_wape_gap": float(project["wape"] / awbe["wape"] - 1.0),
        "project_within_five_wape_points": bool(project["wape"] <= awbe["wape"] + 0.05),
        "paper_methods_used_by_project": False,
        "paper_component_predictions_used_by_project": False,
        "source_features": "official weekly influenza rate history only",
        "deterministic_replay": bool(deterministic),
        "future_label_sentinel": bool(future_sentinel),
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
