from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

import run_benchmark as bench


ROOT = Path("USER_HOME/projects/openclaw/experiments/natcomm_hk_flu_benchmark_20260823")
POINT = ROOT / "extracted/results_point/Results_point/Point"
OUTPUTS = ROOT / "outputs"
EVAL_START = pd.Timestamp("2023-03-05")
EVAL_END = pd.Timestamp("2024-01-21")

COMPONENT_FILES = {
    "ARIMA": "forecast_ARIMA_rolling_test8_2023.csv",
    "GARCH": "forecast_GARCH_rolling_test8_2023.csv",
    "RF": "forecast_RF_rolling_test8_2023.csv",
    "XGB": "forecast_XGB_rolling_test8_2023.csv",
    "InTimePlus": "forecast_InTimePlus_v3_nontuning_rolling_v2_test8_2023.csv",
    "TSTPlus": "forecast_TSTPlus_v3_nontuning_rolling_v2_test8_2023.csv",
    "LSTM": "forecast_LSTM_v3_nontuning_rolling_v2_test8_2023.csv",
    "GRU": "forecast_GRU_v3_nontuning_rolling_v2_test8_2023.csv",
}

PUBLISHED_FILES = {
    "Baseline": "forecast_baseline_test8_2023.csv",
    "SAE": "forecast_SAE_test8_2023.csv",
    "NBE": "forecast_NBE_test8_2023.csv",
    "AWAE": "forecast_AWAE_test8_2023.csv",
    "AWBE": "forecast_AWBE_test8_2023.csv",
}


def merge_panel(files: dict[str, str]) -> pd.DataFrame:
    result: pd.DataFrame | None = None
    for name, filename in files.items():
        current = bench.load_forecast(POINT / filename, name)
        if result is None:
            result = current
        else:
            result = result.merge(
                current.drop(columns="true"),
                on=["origin", "date", "week_ahead"],
                validate="one_to_one",
            )
    assert result is not None
    return result


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    components = merge_panel(COMPONENT_FILES)
    published = merge_panel(PUBLISHED_FILES)
    post = components.merge(
        published.drop(columns="true"),
        on=["origin", "date", "week_ahead"],
        validate="one_to_one",
    )
    post = post.sort_values(["origin", "week_ahead", "date"]).reset_index(drop=True)

    pre = pd.read_csv(OUTPUTS / "official_prediction_panel.csv", parse_dates=["origin", "date"])
    history_panel = pd.concat([pre, post], ignore_index=True).sort_values(["origin", "week_ahead", "date"])
    evaluation = post[post["date"].between(EVAL_START, EVAL_END)].copy()
    if evaluation.empty:
        raise RuntimeError("empty post-COVID evaluation")

    lock = json.loads((OUTPUTS / "locked_selection.json").read_text())
    raw = lock["selected"]
    selected = bench.Candidate(
        raw["family"], int(raw["window_weeks"]), float(raw["half_life_weeks"]), float(raw["parameter"])
    )
    project = bench.predict_candidate(history_panel, evaluation, selected)
    keys = ["origin", "date", "week_ahead"]
    evaluation = evaluation.merge(
        project[keys + ["prediction", "weights"]], on=keys, validate="one_to_one"
    ).rename(columns={"prediction": "KG_ADAPTIVE_ENSEMBLE"})
    evaluation["KG_HORIZON_ROUTER"] = np.where(
        evaluation["week_ahead"].eq(0), evaluation["KG_ADAPTIVE_ENSEMBLE"], evaluation["AWBE"]
    )
    evaluation.to_csv(OUTPUTS / "postcovid_paired_predictions.csv", index=False)

    rows = []
    for model in list(COMPONENT_FILES) + list(PUBLISHED_FILES) + ["KG_ADAPTIVE_ENSEMBLE", "KG_HORIZON_ROUTER"]:
        rows.append({"model": model, **bench.metric_dict(evaluation["true"], evaluation[model])})
        for horizon, part in evaluation.groupby("week_ahead"):
            rows.append(
                {
                    "model": model,
                    "week_ahead": int(horizon),
                    **bench.metric_dict(part["true"], part[model]),
                }
            )
    metrics = pd.DataFrame(rows)
    metrics.to_csv(OUTPUTS / "postcovid_metrics.csv", index=False)
    overall = metrics[metrics["week_ahead"].isna()].set_index("model")
    summary = {
        "status": "COMPLETE_POSTCOVID_EXTERNAL_SHADOW_TEST",
        "evaluation_target_start": EVAL_START.date().isoformat(),
        "evaluation_target_end": EVAL_END.date().isoformat(),
        "paired_rows": len(evaluation),
        "locked_candidate": selected.name,
        "locked_router_rule": {"horizon_0": "KG_ADAPTIVE_ENSEMBLE", "horizons_1_to_8": "AWBE"},
        "project": overall.loc["KG_ADAPTIVE_ENSEMBLE"].dropna().to_dict(),
        "router": overall.loc["KG_HORIZON_ROUTER"].dropna().to_dict(),
        "awbe": overall.loc["AWBE"].dropna().to_dict(),
        "baseline": overall.loc["Baseline"].dropna().to_dict(),
        "test_used_for_selection": False,
        "database_writes": 0,
        "forecast_runs_created": 0,
        "promotion_changes": 0,
        "active_pointer_changes": 0,
    }
    (OUTPUTS / "postcovid_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    names = ["postcovid_paired_predictions.csv", "postcovid_metrics.csv", "postcovid_summary.json"]
    (OUTPUTS / "postcovid_artifacts.sha256").write_text(
        "\n".join(f"{sha256(OUTPUTS / name)}  {name}" for name in names) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
