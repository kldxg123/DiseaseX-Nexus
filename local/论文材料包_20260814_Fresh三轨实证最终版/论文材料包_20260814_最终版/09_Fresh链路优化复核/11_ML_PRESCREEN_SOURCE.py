from __future__ import annotations

import hashlib
import json
import math
import statistics
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.ensemble import ExtraTreesRegressor, HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.pipeline import make_pipeline

from epidemic_core.kg_r3c.dbutil import connect
from fresh_opt_prescreen import (
    DISEASE_ID,
    METRIC,
    Config as BaselineConfig,
    _calendarize,
    _load_tuning_rows,
    _wape,
    evaluate_block,
)


SEED = 20260814
CANDIDATES = (("MEX", "MX"), ("AUS", "AU"), ("COL", "CO"), ("THA", "TH"), ("NOR", "NO"))
LAGS = (0, 1, 2, 3, 4, 6, 8, 13, 26, 39, 48, 49, 50, 51, 52, 53, 78, 104, 156)
ROLLS = (4, 8, 13, 26, 52)


@dataclass(frozen=True)
class ModelConfig:
    family: str
    loss: str
    learning_rate: float = 0.05
    max_iter: int = 200
    max_leaf_nodes: int = 15
    min_samples_leaf: int = 20
    l2_regularization: float = 1.0
    n_estimators: int = 160
    max_depth: int | None = None
    max_features: float = 1.0
    half_life_weeks: int | None = None


def _hash(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(raw).hexdigest()


def configs() -> list[ModelConfig]:
    out: list[ModelConfig] = []
    for loss in ("poisson", "squared_error"):
        for learning_rate, leaves, min_leaf in (
            (0.03, 15, 20),
            (0.05, 31, 20),
            (0.08, 15, 30),
            (0.05, 63, 40),
        ):
            for half_life in (None, 260):
                out.append(
                    ModelConfig(
                        "hist_gb",
                        loss,
                        learning_rate=learning_rate,
                        max_leaf_nodes=leaves,
                        min_samples_leaf=min_leaf,
                        half_life_weeks=half_life,
                    )
                )
    for loss in ("poisson", "squared_error"):
        for min_leaf, max_features in ((2, 0.7), (5, 1.0)):
            out.append(
                ModelConfig(
                    "extra_trees",
                    loss,
                    min_samples_leaf=min_leaf,
                    n_estimators=160,
                    max_features=max_features,
                    half_life_weeks=260,
                )
            )
    return out


def _safe(values: list[float | None], index: int) -> float:
    if index < 0 or index >= len(values) or values[index] is None:
        return math.nan
    return float(values[index])


def features(values: list[float | None], origin: int, horizon: int) -> list[float]:
    target = origin + horizon
    out = [_safe(values, origin - lag) for lag in LAGS]
    out.extend((_safe(values, target - 52), _safe(values, target - 104), _safe(values, target - 156)))
    for window in ROLLS:
        segment = [_safe(values, idx) for idx in range(origin - window + 1, origin + 1)]
        clean = [value for value in segment if math.isfinite(value)]
        if len(clean) < max(2, math.ceil(window * 0.8)):
            out.extend((math.nan, math.nan, math.nan, math.nan))
        else:
            out.extend((float(np.mean(clean)), float(np.std(clean)), min(clean), max(clean)))
    target_week = target % 52
    out.extend(
        (
            float(horizon),
            math.sin(2.0 * math.pi * target_week / 52.0),
            math.cos(2.0 * math.pi * target_week / 52.0),
            math.sin(4.0 * math.pi * target_week / 52.0),
            math.cos(4.0 * math.pi * target_week / 52.0),
        )
    )
    return out


def training_matrix(
    values: list[float | None], cutoff: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    x: list[list[float]] = []
    y: list[float] = []
    ages: list[int] = []
    for origin in range(156, cutoff):
        if values[origin] is None:
            continue
        for horizon in (1, 2, 3, 4):
            target = origin + horizon
            if target > cutoff or values[target] is None:
                continue
            x.append(features(values, origin, horizon))
            y.append(max(float(values[target]), 0.0))
            ages.append(cutoff - target)
    return np.asarray(x, dtype=float), np.asarray(y, dtype=float), np.asarray(ages, dtype=float)


def fit_model(config: ModelConfig, x: np.ndarray, y: np.ndarray, ages: np.ndarray) -> Any:
    weights = None
    if config.half_life_weeks:
        weights = np.power(0.5, ages / float(config.half_life_weeks))
    if config.family == "hist_gb":
        model = HistGradientBoostingRegressor(
            loss=config.loss,
            learning_rate=config.learning_rate,
            max_iter=config.max_iter,
            max_leaf_nodes=config.max_leaf_nodes,
            min_samples_leaf=config.min_samples_leaf,
            l2_regularization=config.l2_regularization,
            random_state=SEED,
            early_stopping=False,
        )
        model.fit(x, y, sample_weight=weights)
        return model
    model = make_pipeline(
        SimpleImputer(strategy="median", add_indicator=True),
        ExtraTreesRegressor(
            n_estimators=config.n_estimators,
            criterion=config.loss,
            min_samples_leaf=config.min_samples_leaf,
            max_depth=config.max_depth,
            max_features=config.max_features,
            random_state=SEED,
            n_jobs=1,
        ),
    )
    fit_kwargs = {} if weights is None else {"extratreesregressor__sample_weight": weights}
    model.fit(x, y, **fit_kwargs)
    return model


def evaluate_model_block(
    values: list[float | None], block_end: int, config: ModelConfig
) -> dict[str, Any]:
    first_target = block_end - 51
    cutoff = first_target - 1
    x_train, y_train, ages = training_matrix(values, cutoff)
    if len(y_train) < 300:
        return {"wape": None, "paired_n": 0, "reason": "INSUFFICIENT_TRAINING_ROWS"}
    model = fit_model(config, x_train, y_train, ages)
    actual: list[float] = []
    predicted: list[float] = []
    by_horizon: dict[int, tuple[list[float], list[float]]] = {h: ([], []) for h in (1, 2, 3, 4)}
    for target_start in range(first_target, block_end + 1, 4):
        origin = target_start - 1
        for horizon in (1, 2, 3, 4):
            target = origin + horizon
            if target > block_end or values[target] is None:
                continue
            yhat = max(float(model.predict(np.asarray([features(values, origin, horizon)]))[0]), 0.0)
            y = float(values[target])
            actual.append(y)
            predicted.append(yhat)
            by_horizon[horizon][0].append(y)
            by_horizon[horizon][1].append(yhat)
    wape = _wape(actual, predicted)
    return {
        "wape": wape,
        "agreement_pct": None if wape is None else max(0.0, 1.0 - wape) * 100.0,
        "paired_n": len(actual),
        "train_rows": len(y_train),
        "per_horizon_wape": {str(h): _wape(pair[0], pair[1]) for h, pair in by_horizon.items()},
    }


def main() -> None:
    results: list[dict[str, Any]] = []
    model_configs = configs()
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            cur.execute("SET LOCAL statement_timeout = '120s'")
            cur.execute("SET LOCAL idle_in_transaction_session_timeout = '600s'")
            for country_iso3, country_alpha2 in CANDIDATES:
                track_key = f"{country_iso3}:influenza:{METRIC}:week"
                cur.execute(
                    "select count(*) from epidemic_kg.kg_qwen_heldout_registry where track_key = %s",
                    (track_key,),
                )
                if int(cur.fetchone()[0]) != 0:
                    results.append({"track_key": track_key, "status": "EXCLUDED_ALREADY_REGISTERED"})
                    continue
                rows, hidden_meta = _load_tuning_rows(cur, country_alpha2)
                dates, values = _calendarize(rows)
                block_ends = [len(values) - 1 - 52 * offset for offset in (3, 2, 1, 0)]
                ranked: list[tuple[float, float, str, ModelConfig, list[dict[str, Any]]]] = []
                for config in model_configs:
                    selection_blocks = [evaluate_model_block(values, end, config) for end in block_ends[:3]]
                    wapes = [block.get("wape") for block in selection_blocks]
                    if any(value is None for value in wapes) or any(block["paired_n"] < 40 for block in selection_blocks):
                        continue
                    numeric = [float(value) for value in wapes if value is not None]
                    ranked.append(
                        (statistics.median(numeric), max(numeric), _hash(asdict(config)), config, selection_blocks)
                    )
                ranked.sort(key=lambda item: (item[0], item[1], item[2]))
                if not ranked:
                    results.append({"track_key": track_key, "status": "NO_VALID_MODEL", **hidden_meta})
                    continue
                selection_median, selection_worst, config_sha, best_config, selection_blocks = ranked[0]
                latest = evaluate_model_block(values, block_ends[3], best_config)
                baseline_options = []
                for baseline_config in (BaselineConfig("last_value"), BaselineConfig("seasonal_mean", years=1)):
                    metric = evaluate_block(values, block_ends[3], baseline_config)
                    if metric["wape"] is not None:
                        baseline_options.append((float(metric["wape"]), asdict(baseline_config), metric))
                baseline_options.sort(key=lambda item: item[0])
                baseline = baseline_options[0] if baseline_options else None
                latest_wape = latest.get("wape")
                improvement = None
                if baseline is not None and latest_wape is not None and baseline[0] > 0:
                    improvement = (baseline[0] - float(latest_wape)) / baseline[0]
                per_h = latest.get("per_horizon_wape") or {}
                pre_gate = bool(
                    selection_median <= 0.28
                    and selection_worst <= 0.35
                    and latest_wape is not None
                    and float(latest_wape) <= 0.30
                    and improvement is not None
                    and improvement >= 0.01
                    and all(value is None or float(value) <= 0.45 for value in per_h.values())
                )
                results.append(
                    {
                        "track_key": track_key,
                        "status": "ML_PRESCREENED_TUNING_ONLY",
                        "config": asdict(best_config),
                        "config_sha256": config_sha,
                        "selection_median_wape": selection_median,
                        "selection_worst_wape": selection_worst,
                        "selection_blocks": selection_blocks,
                        "latest_pseudo": latest,
                        "latest_best_baseline": None if baseline is None else {
                            "wape": baseline[0], "config": baseline[1], "metrics": baseline[2]
                        },
                        "latest_improvement_vs_baseline": improvement,
                        "pre_gate": pre_gate,
                        "tuning_series_sha256": _hash(
                            [(d.isoformat(), v) for d, v in zip(dates, values, strict=True)]
                        ),
                        **hidden_meta,
                    }
                )
            cur.execute("show transaction_read_only")
            read_only = cur.fetchone()[0]
            conn.rollback()

    eligible = [item for item in results if item.get("pre_gate")]
    eligible.sort(key=lambda item: (item["latest_pseudo"]["wape"], item["track_key"]))
    output = {
        "transaction_read_only": read_only,
        "hidden_values_read": False,
        "selection_protocol": "3 historical 52-week blocks choose config; 4th 52-week block validates chosen config",
        "candidate_count": len(results),
        "pre_gate_count": len(eligible),
        "selected_track_key": eligible[0]["track_key"] if eligible else None,
        "results": results,
    }
    rendered = json.dumps(output, ensure_ascii=False, indent=2)
    if len(sys.argv) == 2:
        Path(sys.argv[1]).write_text(rendered + "\n", encoding="utf-8")
        print(json.dumps({"output": sys.argv[1], "sha256": _hash(output), "pre_gate_count": len(eligible), "selected_track_key": output["selected_track_key"]}))
    else:
        print(rendered)


if __name__ == "__main__":
    main()
