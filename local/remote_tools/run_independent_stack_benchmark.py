from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import nnls


MODELS = (
    "seasonal_52",
    "analog_w8_k8_c05",
    "analog_w13_k8_c10",
    "recursive_hist_poisson",
)


def load_module(path: Path):
    spec = importlib.util.spec_from_file_location("independent_benchmark", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load benchmark module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_validation(base_predictions: Path, recursive_predictions: Path) -> pd.DataFrame:
    base = pd.read_csv(base_predictions)
    recursive = pd.read_csv(
        recursive_predictions,
        usecols=["origin", "date", "week_ahead", "recursive_hist_poisson"],
    )
    return base.merge(
        recursive,
        on=["origin", "date", "week_ahead"],
        how="inner",
        validate="one_to_one",
    )


def fit_lock(module, validation: pd.DataFrame, paths: dict[str, Path]) -> dict[str, object]:
    x = validation[list(MODELS)].to_numpy(dtype=float)
    y = validation["true"].to_numpy(dtype=float)
    scale = np.maximum(np.median(x, axis=0), 1e-6)
    coefficient, _ = nnls(x / scale, y)
    coefficient = coefficient / scale
    if coefficient.sum() <= 0:
        raise RuntimeError("NNLS returned no positive project weights")
    coefficient /= coefficient.sum()
    prediction = x @ coefficient
    lock: dict[str, object] = {
        "status": "LOCKED_BEFORE_STACK_TEST",
        "model_identity": "PROJECT_INDEPENDENT_SEASONAL_ANALOG_STACK_V3",
        "paper_methods_used_as_project_candidates": False,
        "paper_component_predictions_used_as_project_features": False,
        "source_features": "official weekly influenza rate history only",
        "weights": {name: float(value) for name, value in zip(MODELS, coefficient)},
        "validation": module.metrics(y, prediction),
        "validation_origin_start": module.VALIDATION_START.date().isoformat(),
        "validation_origin_end": module.VALIDATION_END.date().isoformat(),
        "selection_method": "global non-negative least squares, normalized to unit sum",
        "input_sha256": {name: module.sha256_file(path) for name, path in paths.items()},
    }
    lock["lock_sha256"] = module.canonical_sha(lock)
    return lock


def independent_test_candidates(module, series, evaluation: pd.DataFrame) -> pd.DataFrame:
    records: list[dict[str, object]] = []
    for row in evaluation.itertuples(index=False):
        origin = pd.Timestamp(row.origin)
        horizon = int(row.week_ahead)
        origin_index = series.date_to_index[origin]
        target_index = origin_index + horizon
        simple = module.predict_simple(series, origin_index, horizon)
        record = {
            "origin": origin,
            "date": pd.Timestamp(row.date),
            "week_ahead": horizon,
            "true": float(row.true),
            "AWBE": float(row.AWBE),
            "Baseline": float(row.Baseline),
            "seasonal_52": float(simple["seasonal_52"]),
        }
        for name in ("analog_w8_k8_c05", "analog_w13_k8_c10"):
            window, neighbors, penalty = module.ANALOG_SPECS[name]
            record[name] = module.analog_prediction(
                series, origin_index, horizon, window, neighbors, penalty
            )
        if abs(float(row.true) - float(series.values[target_index])) > 1e-9:
            raise RuntimeError("label/source mismatch")
        records.append(record)
    return pd.DataFrame(records).sort_values(["origin", "week_ahead", "date"]).reset_index(drop=True)


def combine_test(
    module,
    independent: pd.DataFrame,
    recursive_path: Path,
    lock: dict[str, object],
) -> pd.DataFrame:
    recursive = pd.read_csv(
        recursive_path,
        usecols=["origin", "date", "week_ahead", "recursive_hist_poisson"],
    )
    recursive["origin"] = pd.to_datetime(recursive["origin"])
    recursive["date"] = pd.to_datetime(recursive["date"])
    result = independent.merge(
        recursive,
        on=["origin", "date", "week_ahead"],
        how="inner",
        validate="one_to_one",
    )
    if len(result) != len(independent):
        raise RuntimeError("recursive project candidate pairing mismatch")
    result["PROJECT_INDEPENDENT_STACK"] = sum(
        float(lock["weights"][name]) * result[name] for name in MODELS
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=["lock", "test"], required=True)
    parser.add_argument("--module", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--validation-base", type=Path)
    parser.add_argument("--validation-recursive", type=Path)
    parser.add_argument("--lock", type=Path)
    parser.add_argument("--data-zip", type=Path)
    parser.add_argument("--panel", type=Path)
    parser.add_argument("--test-recursive", type=Path)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise RuntimeError(f"output directory already exists: {args.output_dir}")
    args.output_dir.mkdir(parents=True)
    module = load_module(args.module.resolve())

    if args.phase == "lock":
        if args.validation_base is None or args.validation_recursive is None:
            raise RuntimeError("lock phase requires both validation prediction files")
        validation = load_validation(args.validation_base, args.validation_recursive)
        paths = {
            "validation_base": args.validation_base,
            "validation_recursive": args.validation_recursive,
        }
        lock = fit_lock(module, validation, paths)
        module.write_json(args.output_dir / "stack_lock.json", lock)
        prediction = sum(
            float(lock["weights"][name]) * validation[name] for name in MODELS
        )
        validation = validation.copy()
        validation["PROJECT_INDEPENDENT_STACK"] = prediction
        validation.to_csv(args.output_dir / "validation_stack_predictions.csv", index=False)
        summary = {
            "status": "INDEPENDENT_STACK_LOCKED_BEFORE_TEST",
            "lock_sha256": lock["lock_sha256"],
            "weights": lock["weights"],
            "project_validation": lock["validation"],
            "paper_awbe_reference_only": module.metrics(validation["true"], validation["AWBE"]),
            "test_labels_opened_by_this_phase": False,
        }
    else:
        required = [args.lock, args.data_zip, args.panel, args.test_recursive]
        if any(path is None for path in required):
            raise RuntimeError("test phase requires lock, data, panel and recursive predictions")
        lock = json.loads(args.lock.read_text(encoding="utf-8"))
        expected = str(lock.pop("lock_sha256"))
        if module.canonical_sha(lock) != expected:
            raise RuntimeError("stack lock SHA mismatch")
        lock["lock_sha256"] = expected
        series = module.load_series(args.data_zip)
        evaluation = module.load_evaluation_panel(args.panel, "test")
        independent = independent_test_candidates(module, series, evaluation)
        result = combine_test(module, independent, args.test_recursive, lock)
        result.to_csv(args.output_dir / "test_stack_predictions.csv", index=False)
        project = module.metrics(result["true"], result["PROJECT_INDEPENDENT_STACK"])
        awbe = module.metrics(result["true"], result["AWBE"])
        by_horizon: list[dict[str, object]] = []
        for horizon, part in result.groupby("week_ahead"):
            by_horizon.append(
                {
                    "week_ahead": int(horizon),
                    "model": "PROJECT_INDEPENDENT_STACK",
                    **module.metrics(part["true"], part["PROJECT_INDEPENDENT_STACK"]),
                }
            )
            by_horizon.append(
                {
                    "week_ahead": int(horizon),
                    "model": "AWBE_REFERENCE_ONLY",
                    **module.metrics(part["true"], part["AWBE"]),
                }
            )
        pd.DataFrame(by_horizon).to_csv(args.output_dir / "test_metrics_by_horizon.csv", index=False)
        summary = {
            "status": (
                "INDEPENDENT_PROJECT_WITHIN_COMPARABILITY_TARGET"
                if project["wape"] <= awbe["wape"] + 0.05
                else "INDEPENDENT_PROJECT_EXPLICIT_PERFORMANCE_GAP"
            ),
            "model_identity": lock["model_identity"],
            "lock_sha256": expected,
            "weights": lock["weights"],
            "project": project,
            "paper_awbe_reference_only": awbe,
            "absolute_wape_gap": float(project["wape"] - awbe["wape"]),
            "relative_wape_gap": float(project["wape"] / awbe["wape"] - 1.0),
            "project_within_five_wape_points": bool(project["wape"] <= awbe["wape"] + 0.05),
            "paper_methods_used_by_project": False,
            "paper_component_predictions_used_by_project": False,
            "paired_rows": int(len(result)),
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
