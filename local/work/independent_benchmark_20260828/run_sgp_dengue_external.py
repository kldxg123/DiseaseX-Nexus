"""Locked paper-independent Singapore dengue external evaluation."""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np

SEED = 20260828


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_previous_module(path: Path):
    spec = importlib.util.spec_from_file_location("sgp_previous", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load previous benchmark module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def read_values(path: Path) -> tuple[list[dict[str, str]], list[float]]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    return rows, [float(row["cases"]) for row in rows]


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def bootstrap(
    score,
    actual: np.ndarray,
    project: np.ndarray,
    paper: np.ndarray,
) -> dict[str, float | int]:
    rng = np.random.default_rng(SEED)
    draws = 5000
    block = 13
    deltas = []
    for _ in range(draws):
        indices: list[int] = []
        while len(indices) < len(actual):
            start = int(rng.integers(0, len(actual)))
            indices.extend((start + offset) % len(actual) for offset in range(block))
        index = np.asarray(indices[: len(actual)], dtype=int)
        delta = score(actual[index], project[index])["wape"]
        delta -= score(actual[index], paper[index])["wape"]
        deltas.append(delta)
    values = np.asarray(deltas)
    low, high = np.quantile(values, [0.025, 0.975])
    return {
        "draws": draws,
        "block_length_weeks": block,
        "lower_95": float(low),
        "upper_95": float(high),
        "probability_project_not_worse": float(np.mean(values <= 0.0)),
        "probability_within_3pp": float(np.mean(values <= 0.03)),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--source-json", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, required=True)
    args = parser.parse_args()

    lock = json.loads(args.lock.read_text(encoding="utf-8"))
    if lock["status"] != "LOCKED_BEFORE_EXTERNAL_LABEL_ACCESS":
        raise RuntimeError("invalid pre-label lock")
    if lock["project_output"]["paper_method_weight"] != 0.0:
        raise RuntimeError("project output is not independent")

    prior = (
        args.repository_root
        / "reports"
        / "post2024_singapore_dengue_paper_aligned_20260813_final"
    )
    paths = {
        "selection_series_sha256": prior
        / "source"
        / "singapore_dengue_official_paper_aligned_2012_2019.csv",
        "selection_grid_sha256": prior / "ensemble_grid_2024_official_overlap.csv",
        "historical_test_predictions_sha256": prior
        / "paired_predictions_2024_official_overlap.csv",
    }
    for key, path in paths.items():
        if digest(path).lower() != lock["source"][key].lower():
            raise RuntimeError(f"hash mismatch: {path}")

    payload = json.loads(args.source_json.read_text(encoding="utf-8"))
    official = [
        row
        for row in payload["result"]["records"]
        if row["disease"] == "Dengue Fever"
    ]
    official.sort(key=lambda row: row["epi_week"])
    if official[0]["epi_week"] != "2012-W01":
        raise RuntimeError("unexpected official start")
    if official[-1]["epi_week"] != "2022-W52":
        raise RuntimeError("unexpected official end")

    source_rows, selection_values = read_values(paths["selection_series_sha256"])
    official_values = {
        row["epi_week"]: float(row["no._of_cases"])
        for row in official
    }
    for index, row in enumerate(source_rows):
        key = f"{int(row['year']):04d}-W{int(row['week']):02d}"
        if selection_values[index] != official_values[key]:
            raise RuntimeError(f"overlap mismatch: {key}")

    context = [
        float(row["no._of_cases"])
        for row in official
        if "2019-W49" <= row["epi_week"] < "2020-W01"
    ]
    external_rows = [
        row
        for row in official
        if "2020-W01" <= row["epi_week"] <= "2022-W52"
    ]
    actual = [float(row["no._of_cases"]) for row in external_rows]
    if len(actual) != 157:
        raise RuntimeError(f"expected 157 external rows, got {len(actual)}")

    module = load_previous_module(prior / "code" / "run_2024_method_comparison.py")
    history = selection_values + context
    paper_spec = lock["paper"]["comparator"]
    coefficients = (
        float(paper_spec["intercept"]),
        float(paper_spec["lag1"]),
        float(paper_spec["lag2"]),
    )
    paper_prediction = module.rolling_fixed_ar2(history, actual, coefficients)

    components = {
        item["name"]: item
        for item in lock["project_output"]["components"]
    }
    boost = components["lagged_count_boosting.v1"]
    boost_prediction = module.rolling_project(history, actual, boost["parameters"])
    last_prediction = module.rolling_last_value(history, actual)
    project_prediction = [
        float(boost["weight"]) * first
        + float(components["last_value"]["weight"]) * second
        for first, second in zip(boost_prediction, last_prediction, strict=True)
    ]

    arrays = {
        "paper_ar2": np.asarray(paper_prediction),
        "project_independent": np.asarray(project_prediction),
        "project_lagged_count_boosting": np.asarray(boost_prediction),
        "last_value": np.asarray(last_prediction),
    }
    actual_array = np.asarray(actual)
    overall = {
        name: module.score(actual_array, prediction)
        for name, prediction in arrays.items()
    }
    delta = overall["project_independent"]["wape"] - overall["paper_ar2"]["wape"]
    margin = lock["external_evaluation"]["comparable_gate"][
        "maximum_absolute_wape_gap_percentage_points"
    ] / 100.0
    passed = bool(delta <= margin)

    paired = []
    for row, observed, paper_value, boost_value, last_value, project_value in zip(
        external_rows,
        actual,
        paper_prediction,
        boost_prediction,
        last_prediction,
        project_prediction,
        strict=True,
    ):
        year, week = row["epi_week"].split("-W")
        paired.append(
            {
                "epi_week": row["epi_week"],
                "year": int(year),
                "week": int(week),
                "actual": observed,
                "paper_ar2": paper_value,
                "project_lagged_count_boosting": boost_value,
                "last_value": last_value,
                "project_independent": project_value,
            }
        )

    yearly = []
    for year in (2020, 2021, 2022):
        mask = np.asarray([row["year"] == year for row in paired])
        yearly.append(
            {
                "year": year,
                "rows": int(mask.sum()),
                "paper_wape": module.score(
                    actual_array[mask], arrays["paper_ar2"][mask]
                )["wape"],
                "project_wape": module.score(
                    actual_array[mask], arrays["project_independent"][mask]
                )["wape"],
                "last_value_wape": module.score(
                    actual_array[mask], arrays["last_value"][mask]
                )["wape"],
            }
        )

    status = (
        "INDEPENDENT_EXTERNAL_COMPARABILITY_CONFIRMED"
        if passed
        else "INDEPENDENT_EXTERNAL_COMPARABILITY_NOT_CONFIRMED"
    )
    summary = {
        "status": status,
        "evaluation_identity": lock["evaluation_identity"],
        "independent_of_paper_method": True,
        "paper_method_weight_in_project_output": 0.0,
        "external_rows": len(actual),
        "external_range": {
            "start": external_rows[0]["epi_week"],
            "end": external_rows[-1]["epi_week"],
        },
        "primary_metric": "WAPE",
        "overall_metrics": overall,
        "comparison": {
            "project_minus_paper_wape": delta,
            "absolute_gap_percentage_points": 100.0 * delta,
            "relative_wape_change": (
                overall["project_independent"]["wape"]
                / overall["paper_ar2"]["wape"]
                - 1.0
            ),
            "comparability_margin_percentage_points": 100.0 * margin,
            "comparability_gate_pass": passed,
        },
        "paired_block_bootstrap": bootstrap(
            module.score,
            actual_array,
            arrays["project_independent"],
            arrays["paper_ar2"],
        ),
        "per_year": yearly,
        "provenance": {
            "lock_sha256": digest(args.lock),
            "official_source_json_sha256": digest(args.source_json),
            "selection_series_sha256": digest(paths["selection_series_sha256"]),
        },
        "limitations": [
            "Fresh relative to this lock, but not globally preregistered.",
            "Comparator is the published fixed AR(2) equation.",
            "Official catalogue ends in 2022.",
        ],
    }

    args.output.mkdir(parents=True, exist_ok=True)
    write_csv(args.output / "paired_predictions.csv", paired)
    write_csv(args.output / "metrics_by_year.csv", yearly)
    (args.output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
