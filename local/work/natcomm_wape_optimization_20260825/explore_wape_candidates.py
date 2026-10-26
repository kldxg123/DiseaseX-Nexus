from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import nnls


ROOT = Path(__file__).resolve().parent
PANEL = ROOT / "outputs" / "official_prediction_panel.csv"
COMPONENTS = ["ARIMA", "GARCH", "RF", "XGB", "InTimePlus", "TSTPlus", "LSTM", "GRU"]


@dataclass(frozen=True)
class Candidate:
    family: str
    window: int
    half_life: float
    ridge: float = 0.0
    iterations: int = 1

    @property
    def name(self) -> str:
        return (
            f"{self.family}_w{self.window}_hl{self.half_life:g}"
            f"_r{self.ridge:g}_i{self.iterations}"
        )


def wape(actual: np.ndarray, predicted: np.ndarray) -> float:
    return float(np.abs(actual - predicted).sum() / np.abs(actual).sum())


def weighted_nnls(
    x: np.ndarray,
    y: np.ndarray,
    sample_weight: np.ndarray,
    *,
    intercept: bool,
    ridge: float,
    iterations: int,
) -> np.ndarray:
    design = np.column_stack([np.ones(len(x)), x]) if intercept else x
    coef = np.zeros(design.shape[1], dtype=float)
    robust_weight = np.ones(len(y), dtype=float)
    for _ in range(iterations):
        weight = sample_weight * robust_weight
        root = np.sqrt(weight)
        weighted_x = design * root[:, None]
        weighted_y = y * root
        if ridge > 0:
            penalty = np.sqrt(ridge) * np.eye(design.shape[1])
            if intercept:
                penalty[0, 0] = 0.0
            weighted_x = np.vstack([weighted_x, penalty])
            weighted_y = np.concatenate([weighted_y, np.zeros(design.shape[1])])
        coef, _ = nnls(weighted_x, weighted_y)
        residual = np.abs(y - design @ coef)
        scale = max(float(np.median(residual)), 1e-6)
        robust_weight = 1.0 / np.maximum(residual, scale)
    return coef


def predict(panel: pd.DataFrame, rows: pd.DataFrame, candidate: Candidate) -> np.ndarray:
    forecasts: list[float] = []
    for row in rows.itertuples(index=False):
        origin = row.origin
        history = panel[
            panel["week_ahead"].eq(row.week_ahead)
            & panel["date"].lt(origin)
            & panel["date"].ge(origin - pd.Timedelta(weeks=candidate.window))
        ]
        values = np.asarray([getattr(row, name) for name in COMPONENTS], dtype=float)
        if len(history) < len(COMPONENTS) + 2:
            forecasts.append(float(np.mean(values)))
            continue
        age = (origin - history["date"]).dt.days.to_numpy(dtype=float) / 7.0
        weight = np.power(0.5, age / candidate.half_life)
        x = history[COMPONENTS].to_numpy(dtype=float)
        y = history["true"].to_numpy(dtype=float)
        intercept = candidate.family == "affine_nnls"
        coef = weighted_nnls(
            x,
            y,
            weight,
            intercept=intercept,
            ridge=candidate.ridge,
            iterations=candidate.iterations,
        )
        design = np.r_[1.0, values] if intercept else values
        forecasts.append(max(0.0, float(design @ coef)))
    return np.asarray(forecasts)


def main() -> None:
    panel = pd.read_csv(PANEL, parse_dates=["origin", "date"])
    validation = panel[
        panel["origin"].between("2006-11-05", "2007-10-28")
        & panel["date"].le(pd.Timestamp("2007-10-28") + pd.Timedelta(weeks=8))
    ].copy()
    test = panel[
        panel["origin"].between("2007-11-04", "2019-07-14")
        & panel["date"].le("2019-07-14")
        & ~panel["origin"].between("2009-04-05", "2010-03-21")
    ].copy()
    candidates = [
        Candidate(family, window, half_life, ridge, iterations)
        for family in ["scaled_nnls", "affine_nnls"]
        for window in [12, 26, 52, 104]
        for half_life in [4.0, 8.0, 12.0, 26.0]
        for ridge in [0.0, 0.1, 1.0, 10.0]
        for iterations in [1, 3]
    ]
    records: list[dict[str, float | str]] = []
    for number, candidate in enumerate(candidates, start=1):
        validation_prediction = predict(panel, validation, candidate)
        records.append(
            {
                "candidate": candidate.name,
                "validation_wape": wape(validation["true"].to_numpy(), validation_prediction),
            }
        )
        if number % 32 == 0:
            print(f"evaluated {number}/{len(candidates)}")
    result = pd.DataFrame(records).sort_values(["validation_wape", "candidate"])
    best_names = result.head(12)["candidate"].tolist()
    by_name = {candidate.name: candidate for candidate in candidates}
    test_wape: dict[str, float] = {}
    for name in best_names:
        prediction = predict(panel, test, by_name[name])
        test_wape[name] = wape(test["true"].to_numpy(), prediction)
    result["test_wape_diagnostic"] = result["candidate"].map(test_wape)
    result.to_csv(ROOT / "candidate_diagnostic.csv", index=False)
    print(result.head(12).to_string(index=False))


if __name__ == "__main__":
    main()
