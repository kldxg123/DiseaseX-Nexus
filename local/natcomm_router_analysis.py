from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path("USER_HOME/projects/openclaw/experiments/natcomm_hk_flu_benchmark_20260823")
OUTPUTS = ROOT / "outputs"


def metrics(actual: pd.Series, predicted: pd.Series) -> dict[str, float | int]:
    y = actual.to_numpy(float)
    p = predicted.to_numpy(float)
    error = p - y
    absolute = np.abs(error)
    denominator = np.abs(y) + np.abs(p)
    return {
        "n": int(len(y)),
        "rmse": float(np.sqrt(np.mean(error**2))),
        "mae": float(np.mean(absolute)),
        "smape": float(np.mean(np.where(denominator > 0, 2 * absolute / denominator, 0))),
        "wape": float(absolute.sum() / np.abs(y).sum()),
        "agreement": float(max(0, 1 - absolute.sum() / np.abs(y).sum())),
    }


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def main() -> None:
    test = pd.read_csv(OUTPUTS / "test_paired_predictions.csv")
    # This routing rule is justified by development validation only: the locked project
    # ensemble was best for horizon 0, while AWBE was best for horizons 1-8.
    test["KG_HORIZON_ROUTER"] = np.where(
        test["week_ahead"].eq(0), test["KG_ADAPTIVE_ENSEMBLE"], test["AWBE"]
    )
    test[["origin", "date", "week_ahead", "true", "AWBE", "KG_ADAPTIVE_ENSEMBLE", "KG_HORIZON_ROUTER"]].to_csv(
        OUTPUTS / "router_paired_predictions.csv", index=False
    )
    rows = []
    for model in ["Baseline", "AWBE", "KG_ADAPTIVE_ENSEMBLE", "KG_HORIZON_ROUTER"]:
        rows.append({"model": model, **metrics(test["true"], test[model])})
        for horizon, part in test.groupby("week_ahead"):
            rows.append(
                {
                    "model": model,
                    "week_ahead": int(horizon),
                    **metrics(part["true"], part[model]),
                }
            )
    frame = pd.DataFrame(rows)
    frame.to_csv(OUTPUTS / "router_metrics.csv", index=False)
    overall = frame[frame["week_ahead"].isna()].set_index("model")
    router_rmse = float(overall.loc["KG_HORIZON_ROUTER", "rmse"])
    awbe_rmse = float(overall.loc["AWBE", "rmse"])
    summary = {
        "status": "COMPLETE_SHADOW_HORIZON_ROUTER_BENCHMARK",
        "routing_rule": {"horizon_0": "KG_ADAPTIVE_ENSEMBLE", "horizons_1_to_8": "AWBE"},
        "selection_basis": "development validation 2006-11-05 through 2007-10-28",
        "test_used_for_routing_selection": False,
        "router": overall.loc["KG_HORIZON_ROUTER"].dropna().to_dict(),
        "awbe": overall.loc["AWBE"].dropna().to_dict(),
        "relative_rmse_improvement_vs_awbe": 1 - router_rmse / awbe_rmse,
        "independent_from_paper_components": False,
        "interpretation": (
            "The router is an orchestration-layer extension, not an independent base-model replacement: "
            "it deliberately retains the paper AWBE for horizons 1-8."
        ),
        "database_writes": 0,
        "forecast_runs_created": 0,
        "promotion_changes": 0,
        "active_pointer_changes": 0,
    }
    (OUTPUTS / "router_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (OUTPUTS / "router_artifacts.sha256").write_text(
        "\n".join(
            f"{sha256(OUTPUTS / name)}  {name}"
            for name in ["router_paired_predictions.csv", "router_metrics.csv", "router_summary.json"]
        )
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
