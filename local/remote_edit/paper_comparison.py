"""Aiken et al. historical-ILI-only method versus project adapters.

The author data are 37 state ILI percentage series.  This experiment uses the
last 52 weeks as a locked evaluation segment and the preceding 52 weeks as
validation.  Every model sees only the 52 lags of the target state.  GRU is a
documented PyTorch reconstruction (not the original Keras runtime): one GRU
layer with five hidden units, matching the paper's historical-only recurrent
architecture at method level.  Model selection uses validation only.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import random
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import torch
from torch import nn

from epidemic_core.kg_qwen import adapters_ext
from epidemic_core.kg_r3c.execution import TrainingRow, fit_model, predict_model, sha256_json

ROOT = Path("USER_HOME/projects/openclaw")
DATA = ROOT / "storage/cache/paper_benchmark/States_ILI.csv"
OUT = ROOT / "reports/paper_comparison_aiken_20260813"
SEED = 20260813
LAG = 52
VAL_N = 52
TEST_N = 52
HORIZON = 1


def file_sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pairs = [(float(r["actual"]), float(r["yhat"])) for r in rows]
    n = len(pairs)
    ae = [abs(a - p) for a, p in pairs]
    se = [(a - p) ** 2 for a, p in pairs]
    denom = sum(abs(a) for a, _ in pairs)
    mean_a = sum(a for a, _ in pairs) / n
    mean_p = sum(p for _, p in pairs) / n
    cov = sum((a - mean_a) * (p - mean_p) for a, p in pairs)
    va = sum((a - mean_a) ** 2 for a, _ in pairs)
    vp = sum((p - mean_p) ** 2 for _, p in pairs)
    wape = sum(ae) / denom if denom else None
    return {
        "n": n,
        "mae": sum(ae) / n,
        "rmse": math.sqrt(sum(se) / n),
        "wape": wape,
        "agreement_pct": None if wape is None else max(0.0, 100.0 * (1.0 - wape)),
        "pearson_r": cov / math.sqrt(va * vp) if va > 0 and vp > 0 else None,
    }


class SmallGRU(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.gru = nn.GRU(1, 5, batch_first=True)
        self.out = nn.Linear(5, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        y, _ = self.gru(x)
        return self.out(y[:, -1, :]).squeeze(-1)


@dataclass
class GRUFit:
    model: SmallGRU
    mean: float
    std: float
    epochs: int


def fit_gru(values: list[float], epochs: int) -> GRUFit:
    torch.manual_seed(SEED)
    random.seed(SEED)
    mean = sum(values) / len(values)
    std = math.sqrt(sum((v - mean) ** 2 for v in values) / len(values)) or 1.0
    norm = [(v - mean) / std for v in values]
    xs = torch.tensor([[norm[i - LAG : i]] for i in range(LAG, len(norm))], dtype=torch.float32).transpose(1, 2)
    ys = torch.tensor([norm[i] for i in range(LAG, len(norm))], dtype=torch.float32)
    model = SmallGRU()
    opt = torch.optim.Adam(model.parameters(), lr=0.001)
    loss_fn = nn.MSELoss()
    for _ in range(epochs):
        opt.zero_grad()
        loss = loss_fn(model(xs), ys)
        loss.backward()
        opt.step()
    return GRUFit(model, mean, std, epochs)


def predict_gru(fit: GRUFit, history: list[float]) -> float:
    x = torch.tensor([[(v - fit.mean) / fit.std for v in history[-LAG:]]], dtype=torch.float32).unsqueeze(-1)
    fit.model.eval()
    with torch.no_grad():
        y = float(fit.model(x).item()) * fit.std + fit.mean
    return max(0.0, y)


def load() -> tuple[list[datetime], dict[str, list[float]]]:
    with DATA.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
    dates = [datetime.strptime(r["date"], "%m/%d/%y") for r in rows]
    states = {s: [float(r[s]) for r in rows] for s in reader.fieldnames or [] if s != "date"}
    return dates, states


def walk_gru(values: list[float], origins: range, epochs: int) -> list[float]:
    out = []
    for origin in origins:
        fit = fit_gru(values[: origin + 1], epochs)
        out.append(predict_gru(fit, values[: origin + 1]))
    return out


def walk_project(adapter: str, values: list[float], dates: list[datetime], origins: range, params: dict[str, Any]) -> list[float]:
    adapters_ext.register_extended_models()
    rows = [TrainingRow(d.date(), None, v, f"aiken:{i}") for i, (d, v) in enumerate(zip(dates, values, strict=True))]
    out = []
    for origin in origins:
        fitted = fit_model(adapter, rows[: origin + 1], params, SEED, dates[origin].date(), "week")
        pred = predict_model(fitted, [1], dates[origin].date())[0]["yhat"]
        out.append(float(pred) if pred is not None else float("nan"))
    return out


def main() -> None:
    torch.set_num_threads(12)
    OUT.mkdir(parents=True, exist_ok=True)
    dates, states = load()
    # Select epochs on validation only; test remains unopened until all model
    # and parameter choices are fixed.
    val_start = len(dates) - TEST_N - VAL_N
    test_start = len(dates) - TEST_N
    val_origins = range(val_start - 1, test_start - 1)
    test_origins = range(test_start - 1, len(dates) - 1)
    state = "NY"  # fixed, present in the 37-state author file; not selected by test performance
    values = states[state]
    # The paper/repository fixes this simple GRU at five hidden units and 700
    # epochs; do not tune the paper method to this evaluation window.
    epoch_grid = [700]
    validation: dict[str, Any] = {}
    best_epochs = None
    best_wape = float("inf")
    for epochs in epoch_grid:
        yhats = walk_gru(values, val_origins, epochs)
        rows = [{"actual": values[o + 1], "yhat": y} for o, y in zip(val_origins, yhats, strict=True)]
        m = metrics(rows)
        validation[f"gru_{epochs}"] = m
        if m["wape"] is not None and m["wape"] < best_wape:
            best_wape = m["wape"]
            best_epochs = epochs
    assert best_epochs is not None
    project_models = {
        "last_value": {},
        "seasonal_naive": {},
        "lagged_count_boosting.v1": {"n_estimators": 37.0, "learning_rate": 0.1601249187005262, "min_samples_leaf": 25.0},
        "fourier_negbin_glm.v1": {"K": 2, "trend": 1, "ridge": 0.001, "shape": 10.0},
    }
    for adapter, params in project_models.items():
        yhats = walk_project(adapter, values, dates, val_origins, params)
        rows = [{"actual": values[o + 1], "yhat": y} for o, y in zip(val_origins, yhats, strict=True) if math.isfinite(y)]
        validation[adapter] = metrics(rows)
    # Final test: each fixed method is evaluated exactly once on identical rows.
    test_predictions: list[dict[str, Any]] = []
    gru_y = walk_gru(values, test_origins, best_epochs)
    for origin, yhat in zip(test_origins, gru_y, strict=True):
        test_predictions.append({"model": "aiken_gru_reconstruction", "state": state, "origin": dates[origin].date().isoformat(), "target": dates[origin + 1].date().isoformat(), "actual": values[origin + 1], "yhat": yhat})
    for adapter, params in project_models.items():
        yhats = walk_project(adapter, values, dates, test_origins, params)
        for origin, yhat in zip(test_origins, yhats, strict=True):
            if math.isfinite(yhat):
                test_predictions.append({"model": adapter, "state": state, "origin": dates[origin].date().isoformat(), "target": dates[origin + 1].date().isoformat(), "actual": values[origin + 1], "yhat": yhat})
    models = sorted({r["model"] for r in test_predictions})
    test_metrics = {m: metrics([r for r in test_predictions if r["model"] == m]) for m in models}
    csv_path = OUT / "paired_predictions.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["model", "state", "origin", "target", "actual", "yhat"])
        writer.writeheader(); writer.writerows(test_predictions)
    report = {
        "experiment_identity": "AIKEN_HISTORICAL_ILI_ONLY_METHOD_RECONSTRUCTION_VS_PROJECT",
        "paper": {"doi": "10.1126/sciadv.abb1237", "kg_candidate_prefix": "7b8ea8a8", "method": "GRU, 52 lags, 5 hidden units", "original_runtime_reproduced": False, "reconstruction_runtime": f"torch {torch.__version__}", "warning": "The original repository uses legacy Keras. This is a method-level PyTorch reconstruction, not an exact original-runtime reproduction."},
        "data": {"source": "author GitHub raw States_ILI.csv", "sha256": file_sha(DATA), "rows": len(dates), "states": len(states), "selected_state": state, "selection_rule": "fixed New York because it exists in the 37-state file; no test-based state selection"},
        "split": {"lag_weeks": LAG, "validation_weeks": VAL_N, "test_weeks": TEST_N, "validation_start": dates[val_start].date().isoformat(), "validation_end": dates[test_start - 1].date().isoformat(), "test_start": dates[test_start].date().isoformat(), "test_end": dates[-1].date().isoformat(), "horizon": HORIZON},
        "selected_gru_epochs": best_epochs,
        "validation_metrics": validation,
        "test_metrics": test_metrics,
        "paired_predictions_sha256": file_sha(csv_path),
        "all_models_same_test_rows": all(v["n"] == TEST_N for v in test_metrics.values()),
        "status": "METHOD_LEVEL_RECONSTRUCTION_COMPLETE_ORIGINAL_RUNTIME_NOT_REPRODUCED",
    }
    (OUT / "comparison.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "comparison.sha256").write_text(f"{file_sha(OUT / 'comparison.json')}  comparison.json\n{file_sha(csv_path)}  paired_predictions.csv\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
