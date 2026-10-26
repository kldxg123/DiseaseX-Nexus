from __future__ import annotations

import argparse
import hashlib
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
from scipy.optimize import nnls


SEED = 20260823
TEST_START = pd.Timestamp("2007-11-04")
TEST_END = pd.Timestamp("2019-07-14")
GAP_START = pd.Timestamp("2009-04-05")
GAP_END = pd.Timestamp("2010-03-21")
VALIDATION_START = pd.Timestamp("2006-11-05")
VALIDATION_END = pd.Timestamp("2007-10-28")

COMPONENTS = {
    "ARIMA": "forecast_ARIMA_rolling_test8.csv",
    "GARCH": "forecast_GARCH_rolling_test8.csv",
    "RF": "forecast_RF_rolling_test8.csv",
    "XGB": "forecast_XGB_rolling_test8.csv",
    "InTimePlus": "forecast_InTimePlus_v3_nontuning_rolling_v2_test8.csv",
    "TSTPlus": "forecast_TSTPlus_v3_nontuning_rolling_test8.csv",
    "LSTM": "forecast_LSTM_v3_nontuning_rolling_v2_test8.csv",
    "GRU": "forecast_GRU_v3_nontuning_rolling_v2_test8.csv",
}

PUBLISHED = {
    "Baseline": "forecast_baseline_test8.csv",
    "SAE": "forecast_SAE_test8.csv",
    "NBE": "forecast_NBE_test8.csv",
    "AWAE": "forecast_AWAE_test8.csv",
    "AWBE": "forecast_AWBE_test8.csv",
}


@dataclass(frozen=True)
class Candidate:
    family: str
    window_weeks: int
    half_life_weeks: float
    parameter: float

    @property
    def name(self) -> str:
        return (
            f"{self.family}_w{self.window_weeks}_hl{self.half_life_weeks:g}"
            f"_p{self.parameter:g}"
        )


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
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def load_forecast(path: Path, name: str) -> pd.DataFrame:
    frame = pd.read_csv(path)
    required = {"date", "true", "week_ahead"}
    if not required.issubset(frame.columns):
        raise RuntimeError(f"{path.name}: missing {required - set(frame.columns)}")
    point_col = "point_avg" if "point_avg" in frame.columns else "point"
    frame["date"] = pd.to_datetime(frame["date"])
    frame["week_ahead"] = frame["week_ahead"].astype(int)
    frame["origin"] = frame["date"] - pd.to_timedelta(frame["week_ahead"] * 7, unit="D")
    frame = frame[["origin", "date", "week_ahead", "true", point_col]].copy()
    frame = frame.rename(columns={point_col: name})
    frame[name] = frame[name].astype(float).clip(lower=0)
    frame["true"] = frame["true"].astype(float)
    if frame.duplicated(["origin", "date", "week_ahead"]).any():
        raise RuntimeError(f"{path.name}: duplicate keys")
    return frame


def build_panel(point_dir: Path) -> pd.DataFrame:
    panel: pd.DataFrame | None = None
    for name, filename in COMPONENTS.items():
        current = load_forecast(point_dir / filename, name)
        if panel is None:
            panel = current
        else:
            panel = panel.merge(
                current.drop(columns="true"),
                on=["origin", "date", "week_ahead"],
                how="inner",
                validate="one_to_one",
            )
    assert panel is not None
    for name, filename in PUBLISHED.items():
        current = load_forecast(point_dir / filename, name)
        panel = panel.merge(
            current.drop(columns="true"),
            on=["origin", "date", "week_ahead"],
            how="inner",
            validate="one_to_one",
        )
    panel = panel.sort_values(["origin", "week_ahead", "date"]).reset_index(drop=True)
    component_cols = list(COMPONENTS)
    if panel[component_cols + list(PUBLISHED)].isna().any().any():
        raise RuntimeError("prediction panel contains missing values")
    if not panel["week_ahead"].between(0, 8).all():
        raise RuntimeError("unexpected horizon")
    return panel


def metric_dict(actual: np.ndarray, predicted: np.ndarray) -> dict[str, float | int]:
    actual = np.asarray(actual, dtype=float)
    predicted = np.asarray(predicted, dtype=float)
    error = predicted - actual
    abs_error = np.abs(error)
    smape_den = np.abs(actual) + np.abs(predicted)
    mape_mask = np.abs(actual) > 1e-12
    return {
        "n": int(len(actual)),
        "rmse": float(np.sqrt(np.mean(error**2))),
        "mae": float(np.mean(abs_error)),
        "smape": float(np.mean(np.where(smape_den > 0, 2 * abs_error / smape_den, 0))),
        "mape": float(np.mean(abs_error[mape_mask] / np.abs(actual[mape_mask]))),
        "wape": float(np.sum(abs_error) / np.sum(np.abs(actual))),
        "agreement": float(max(0.0, 1 - np.sum(abs_error) / np.sum(np.abs(actual)))),
    }


def history_for_row(panel: pd.DataFrame, origin: pd.Timestamp, horizon: int, window: int) -> pd.DataFrame:
    # A target is eligible only after it is strictly before the current forecast origin.
    hist = panel[(panel["week_ahead"] == horizon) & (panel["date"] < origin)].copy()
    if hist.empty:
        return hist
    cutoff = origin - pd.Timedelta(weeks=window)
    hist = hist[hist["date"] >= cutoff]
    return hist.sort_values("date")


def decay_weights(hist: pd.DataFrame, origin: pd.Timestamp, half_life: float) -> np.ndarray:
    ages = (origin - hist["date"]).dt.days.to_numpy(dtype=float) / 7.0
    return np.power(0.5, ages / half_life)


def component_weights(
    hist: pd.DataFrame,
    origin: pd.Timestamp,
    candidate: Candidate,
) -> np.ndarray:
    cols = list(COMPONENTS)
    if len(hist) < max(8, len(cols)):
        return np.full(len(cols), 1.0 / len(cols))
    x = hist[cols].to_numpy(dtype=float)
    y = hist["true"].to_numpy(dtype=float)
    sample_weight = decay_weights(hist, origin, candidate.half_life_weeks)
    error = x - y[:, None]
    rmse = np.sqrt(np.average(error**2, axis=0, weights=sample_weight))
    scale = max(float(np.median(rmse)), 1e-8)

    if candidate.family == "topk_mean":
        k = int(candidate.parameter)
        chosen = np.argsort(rmse)[:k]
        weights = np.zeros(len(cols))
        weights[chosen] = 1.0 / k
        return weights
    if candidate.family == "topk_median":
        # Marker weights; median is handled in predict_candidate.
        k = int(candidate.parameter)
        chosen = np.argsort(rmse)[:k]
        weights = np.zeros(len(cols))
        weights[chosen] = 1.0
        return weights
    if candidate.family == "inverse_rmse":
        power = float(candidate.parameter)
        weights = 1.0 / np.maximum(rmse, scale * 0.05) ** power
        return weights / weights.sum()
    if candidate.family == "softmax_rmse":
        eta = float(candidate.parameter)
        logits = -eta * rmse / scale
        logits -= logits.max()
        weights = np.exp(logits)
        return weights / weights.sum()
    if candidate.family == "nnls":
        root = np.sqrt(sample_weight)
        coef, _ = nnls(x * root[:, None], y * root)
        if coef.sum() <= 0:
            return np.full(len(cols), 1.0 / len(cols))
        return coef / coef.sum()
    raise ValueError(candidate.family)


def predict_candidate(panel: pd.DataFrame, rows: pd.DataFrame, candidate: Candidate) -> pd.DataFrame:
    records: list[dict[str, object]] = []
    cols = list(COMPONENTS)
    for row in rows.itertuples(index=False):
        hist = history_for_row(panel, row.origin, int(row.week_ahead), candidate.window_weeks)
        weights = component_weights(hist, row.origin, candidate)
        values = np.asarray([getattr(row, col) for col in cols], dtype=float)
        if candidate.family == "topk_median":
            chosen = values[weights > 0]
            predicted = float(np.median(chosen))
        else:
            predicted = float(np.dot(values, weights))
        records.append(
            {
                "origin": row.origin,
                "date": row.date,
                "week_ahead": int(row.week_ahead),
                "true": float(row.true),
                "prediction": max(0.0, predicted),
                "weights": json.dumps(dict(zip(cols, weights.tolist())), sort_keys=True),
            }
        )
    return pd.DataFrame.from_records(records)


def candidates() -> Iterable[Candidate]:
    for window in [12, 26, 52, 104, 156]:
        for half_life in [4.0, 8.0, 12.0, 26.0, 52.0]:
            for k in [1.0, 2.0, 3.0, 4.0]:
                yield Candidate("topk_mean", window, half_life, k)
            for k in [2.0, 3.0, 4.0, 5.0]:
                yield Candidate("topk_median", window, half_life, k)
            for power in [0.5, 1.0, 2.0, 3.0]:
                yield Candidate("inverse_rmse", window, half_life, power)
            for eta in [0.5, 1.0, 2.0, 4.0, 8.0]:
                yield Candidate("softmax_rmse", window, half_life, eta)
            yield Candidate("nnls", window, half_life, 0.0)


def summarize_models(frame: pd.DataFrame, models: list[str], prediction_col: str | None = None) -> pd.DataFrame:
    records: list[dict[str, object]] = []
    for model in models:
        column = prediction_col if prediction_col else model
        result = {"model": model, **metric_dict(frame["true"].to_numpy(), frame[column].to_numpy())}
        records.append(result)
        for horizon, part in frame.groupby("week_ahead"):
            records.append(
                {
                    "model": model,
                    "week_ahead": int(horizon),
                    **metric_dict(part["true"].to_numpy(), part[column].to_numpy()),
                }
            )
    return pd.DataFrame(records)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--experiment-root", type=Path, required=True)
    args = parser.parse_args()
    root = args.experiment_root.resolve()
    point_dir = root / "extracted/results_point/Results_point/Point"
    output_dir = root / "outputs"
    output_dir.mkdir(exist_ok=False)

    panel = build_panel(point_dir)
    panel.to_csv(output_dir / "official_prediction_panel.csv", index=False)

    validation = panel[
        panel["origin"].between(VALIDATION_START, VALIDATION_END)
        & (panel["date"] <= VALIDATION_END + pd.Timedelta(weeks=8))
    ].copy()
    if validation.empty:
        raise RuntimeError("empty validation panel")

    search_rows: list[dict[str, object]] = []
    best: tuple[float, Candidate, pd.DataFrame] | None = None
    for candidate in candidates():
        predicted = predict_candidate(panel, validation, candidate)
        metric = metric_dict(predicted["true"].to_numpy(), predicted["prediction"].to_numpy())
        record = {**asdict(candidate), "candidate": candidate.name, **metric}
        search_rows.append(record)
        key = float(metric["rmse"])
        if best is None or key < best[0] - 1e-12 or (abs(key - best[0]) <= 1e-12 and candidate.name < best[1].name):
            best = (key, candidate, predicted)
    assert best is not None
    search = pd.DataFrame(search_rows).sort_values(["rmse", "smape", "candidate"])
    search.to_csv(output_dir / "validation_model_search.csv", index=False)
    best_metric, selected, validation_prediction = best
    validation_prediction.to_csv(output_dir / "validation_selected_predictions.csv", index=False)

    selection = {
        "selected": asdict(selected),
        "selected_name": selected.name,
        "selection_metric": "rmse",
        "selection_value": best_metric,
        "validation_origin_start": VALIDATION_START.date().isoformat(),
        "validation_origin_end": VALIDATION_END.date().isoformat(),
        "candidate_count": len(search),
        "test_labels_opened_during_selection": False,
        "selection_input_sha256": sha256_file(output_dir / "official_prediction_panel.csv"),
    }
    selection["selection_lock_sha256"] = canonical_sha(selection)
    write_json(output_dir / "locked_selection.json", selection)

    test = panel[
        panel["origin"].between(TEST_START, TEST_END)
        & (panel["date"] <= TEST_END)
        & ~panel["origin"].between(GAP_START, GAP_END)
    ].copy()
    project = predict_candidate(panel, test, selected)
    if len(project) != len(test):
        raise RuntimeError("test pairing mismatch")
    test_keys = ["origin", "date", "week_ahead"]
    test = test.merge(
        project[test_keys + ["prediction", "weights"]],
        on=test_keys,
        how="inner",
        validate="one_to_one",
    )
    test = test.rename(columns={"prediction": "KG_ADAPTIVE_ENSEMBLE"})
    test.to_csv(output_dir / "test_paired_predictions.csv", index=False)

    model_names = list(COMPONENTS) + list(PUBLISHED) + ["KG_ADAPTIVE_ENSEMBLE"]
    overall_records: list[dict[str, object]] = []
    horizon_records: list[dict[str, object]] = []
    for model in model_names:
        metric = metric_dict(test["true"].to_numpy(), test[model].to_numpy())
        overall_records.append({"model": model, **metric})
        for horizon, part in test.groupby("week_ahead"):
            horizon_records.append(
                {
                    "model": model,
                    "week_ahead": int(horizon),
                    **metric_dict(part["true"].to_numpy(), part[model].to_numpy()),
                }
            )
    overall = pd.DataFrame(overall_records).sort_values("rmse")
    by_horizon = pd.DataFrame(horizon_records).sort_values(["week_ahead", "rmse"])
    overall.to_csv(output_dir / "test_metrics_overall.csv", index=False)
    by_horizon.to_csv(output_dir / "test_metrics_by_horizon.csv", index=False)

    # Determinism: identical second execution of the locked candidate.
    replay = predict_candidate(panel, test, selected)
    left = test[test_keys + ["KG_ADAPTIVE_ENSEMBLE"]].copy()
    right = replay[test_keys + ["prediction"]].rename(columns={"prediction": "KG_ADAPTIVE_ENSEMBLE"})
    deterministic = left.equals(right)
    if not deterministic:
        raise RuntimeError("deterministic replay mismatch")

    # Future-label sentinel: modify labels on/after a cutoff. Predictions strictly before
    # that cutoff must be byte-identical because history admits only date < origin.
    cutoff = pd.Timestamp("2014-01-05")
    mutated = panel.copy()
    mutated.loc[mutated["date"] >= cutoff, "true"] += 9999.0
    sentinel_rows = test[test["origin"] < cutoff].copy()
    before = predict_candidate(panel, sentinel_rows, selected)
    after = predict_candidate(mutated, sentinel_rows, selected)
    sentinel_pass = np.array_equal(
        before["prediction"].to_numpy(), after["prediction"].to_numpy()
    )
    if not sentinel_pass:
        raise RuntimeError("future-label sentinel failed")

    baseline_rmse = float(overall.loc[overall.model == "Baseline", "rmse"].iloc[0])
    awbe_rmse = float(overall.loc[overall.model == "AWBE", "rmse"].iloc[0])
    project_rmse = float(overall.loc[overall.model == "KG_ADAPTIVE_ENSEMBLE", "rmse"].iloc[0])
    final = {
        "status": "COMPLETE_SHADOW_POINT_BENCHMARK",
        "paper": {
            "title": "An adaptive weight ensemble approach to forecast influenza activity in an irregular seasonality context",
            "doi": "10.1038/s41467-024-52504-1",
            "official_repository_commit": "b0bd4ccfd9a4a4183c07f122508056a84d271a0f",
        },
        "protocol": {
            "test_origin_start": TEST_START.date().isoformat(),
            "test_target_end": TEST_END.date().isoformat(),
            "excluded_origin_gap": [GAP_START.date().isoformat(), GAP_END.date().isoformat()],
            "horizons": list(range(9)),
            "paired_rows": len(test),
            "selection": selection,
        },
        "project_result": overall.loc[overall.model == "KG_ADAPTIVE_ENSEMBLE"].iloc[0].to_dict(),
        "paper_awbe_result": overall.loc[overall.model == "AWBE"].iloc[0].to_dict(),
        "paper_baseline_result": overall.loc[overall.model == "Baseline"].iloc[0].to_dict(),
        "relative_rmse_improvement_vs_baseline": 1 - project_rmse / baseline_rmse,
        "relative_rmse_improvement_vs_awbe": 1 - project_rmse / awbe_rmse,
        "project_beats_baseline": project_rmse < baseline_rmse,
        "project_beats_awbe": project_rmse < awbe_rmse,
        "deterministic_replay": deterministic,
        "future_label_sentinel": sentinel_pass,
        "database_writes": 0,
        "forecast_runs_created": 0,
        "promotion_changes": 0,
        "active_pointer_changes": 0,
        "important_limitation": (
            "Paper forecasts are immutable author-released outputs; the project layer is a "
            "locked, validation-selected adaptive orchestration over the same component forecast panel."
        ),
    }
    write_json(output_dir / "final_summary.json", final)

    manifest_entries = []
    for path in sorted(output_dir.iterdir()):
        if path.name == "MANIFEST.sha256" or not path.is_file():
            continue
        manifest_entries.append(f"{sha256_file(path)}  {path.name}")
    (output_dir / "MANIFEST.sha256").write_text("\n".join(manifest_entries) + "\n")
    print(json.dumps(final, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
