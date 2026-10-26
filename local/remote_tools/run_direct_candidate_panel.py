from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

import pandas as pd


def load_module(path: Path):
    spec = importlib.util.spec_from_file_location("independent_benchmark", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load benchmark module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--module", type=Path, required=True)
    parser.add_argument("--data-zip", type=Path, required=True)
    parser.add_argument("--panel", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise RuntimeError(f"output directory already exists: {args.output_dir}")
    args.output_dir.mkdir(parents=True)
    module = load_module(args.module.resolve())
    series = module.load_series(args.data_zip)
    evaluation = module.load_evaluation_panel(args.panel, "test")
    selected = ("hist_poisson_d3", "hist_poisson_d5")
    records: list[dict[str, object]] = []
    for origin, rows in evaluation.groupby("origin", sort=True):
        origin = pd.Timestamp(origin)
        origin_index = series.date_to_index[origin]
        x_train, y_train = module.training_matrix(series, origin_index)
        models = {name: module.model_factories()[name] for name in selected}
        for model in models.values():
            model.fit(x_train, y_train)
        for row in rows.itertuples(index=False):
            horizon = int(row.week_ahead)
            feature = module.feature_row(series, origin_index, horizon)
            if feature is None:
                raise RuntimeError("missing direct feature")
            record: dict[str, object] = {
                "origin": origin,
                "date": pd.Timestamp(row.date),
                "week_ahead": horizon,
                "true": float(row.true),
                "AWBE": float(row.AWBE),
            }
            for name, model in models.items():
                record[name] = max(0.0, float(model.predict(feature.reshape(1, -1))[0]))
            records.append(record)
    output = pd.DataFrame(records).sort_values(["origin", "week_ahead", "date"]).reset_index(drop=True)
    output.to_csv(args.output_dir / "direct_candidate_predictions.csv", index=False)
    summaries: dict[str, object] = {}
    for name in ["AWBE", *selected]:
        summaries[name] = module.metrics(output["true"], output[name])
    summary = {
        "status": "DIRECT_PROJECT_CANDIDATE_PANEL_COMPLETE",
        "paper_methods_used_by_project": False,
        "paper_component_predictions_used_by_project": False,
        "models": summaries,
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
