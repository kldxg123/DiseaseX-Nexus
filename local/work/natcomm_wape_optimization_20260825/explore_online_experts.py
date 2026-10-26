from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent
PANEL = ROOT / "outputs" / "official_prediction_panel.csv"
COMPONENTS = ["ARIMA", "GARCH", "RF", "XGB", "InTimePlus", "TSTPlus", "LSTM", "GRU"]


@dataclass(frozen=True)
class Candidate:
    window: int
    half_life: float
    eta: float
    pooled: bool
    correction: str
    gain: float

    @property
    def name(self) -> str:
        group = "pooled" if self.pooled else "horizon"
        return (
            f"online_l1_{group}_w{self.window}_hl{self.half_life:g}_eta{self.eta:g}"
            f"_{self.correction}{self.gain:g}"
        )


def wape(actual: np.ndarray, predicted: np.ndarray) -> float:
    return float(np.abs(actual - predicted).sum() / np.abs(actual).sum())


def predict(panel: pd.DataFrame, rows: pd.DataFrame, candidate: Candidate) -> np.ndarray:
    forecasts: list[float] = []
    for row in rows.itertuples(index=False):
        origin = row.origin
        if candidate.pooled and row.week_ahead > 0:
            horizon_mask = panel["week_ahead"].between(1, 8)
        else:
            horizon_mask = panel["week_ahead"].eq(row.week_ahead)
        history = panel[
            horizon_mask
            & panel["date"].lt(origin)
            & panel["date"].ge(origin - pd.Timedelta(weeks=candidate.window))
        ]
        values = np.asarray([getattr(row, name) for name in COMPONENTS], dtype=float)
        if len(history) < 10:
            forecasts.append(float(np.mean(values)))
            continue
        x = history[COMPONENTS].to_numpy(dtype=float)
        y = history["true"].to_numpy(dtype=float)
        age = (origin - history["date"]).dt.days.to_numpy(dtype=float) / 7.0
        recency = np.power(0.5, age / candidate.half_life)
        recency /= recency.sum()
        absolute_error = np.abs(x - y[:, None])
        l1_risk = np.sum(absolute_error * recency[:, None], axis=0)
        scale = max(float(np.median(l1_risk)), 1e-8)
        logits = -candidate.eta * l1_risk / scale
        logits -= logits.max()
        weights = np.exp(logits)
        weights /= weights.sum()

        adjusted = values.copy()
        if candidate.correction == "bias":
            bias = np.sum((y[:, None] - x) * recency[:, None], axis=0)
            adjusted += candidate.gain * bias
        elif candidate.correction == "ratio":
            denominator = np.sum((x**2) * recency[:, None], axis=0)
            slope = np.divide(
                np.sum((x * y[:, None]) * recency[:, None], axis=0),
                denominator,
                out=np.ones_like(denominator),
                where=denominator > 1e-12,
            )
            slope = np.clip(slope, 0.5, 2.0)
            adjusted *= 1.0 + candidate.gain * (slope - 1.0)
        forecasts.append(max(0.0, float(adjusted @ weights)))
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
        Candidate(window, half_life, eta, pooled, correction, gain)
        for window in [12, 26, 52, 104]
        for half_life in [2.0, 4.0, 8.0, 16.0, 32.0]
        for eta in [0.5, 1.0, 2.0, 4.0]
        for pooled in [False, True]
        for correction, gain in [
            ("none", 0.0),
            ("bias", 0.5),
            ("bias", 1.0),
            ("ratio", 0.5),
            ("ratio", 1.0),
        ]
    ]
    records: list[dict[str, float | str]] = []
    for number, candidate in enumerate(candidates, start=1):
        prediction = predict(panel, validation, candidate)
        records.append(
            {
                "candidate": candidate.name,
                "validation_wape": wape(validation["true"].to_numpy(), prediction),
            }
        )
        if number % 80 == 0:
            print(f"evaluated {number}/{len(candidates)}", flush=True)
    result = pd.DataFrame(records).sort_values(["validation_wape", "candidate"])
    best_names = result.head(12)["candidate"].tolist()
    by_name = {candidate.name: candidate for candidate in candidates}
    test_wape: dict[str, float] = {}
    for name in best_names:
        prediction = predict(panel, test, by_name[name])
        test_wape[name] = wape(test["true"].to_numpy(), prediction)
    result["test_wape_diagnostic"] = result["candidate"].map(test_wape)
    result.to_csv(ROOT / "online_expert_diagnostic.csv", index=False)
    print(result.head(12).to_string(index=False))


if __name__ == "__main__":
    main()
