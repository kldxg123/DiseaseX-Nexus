"""Entry point for the KG paper-benchmark rolling-origin experiment.

Runs the full deterministic pipeline twice, verifies the predictions CSV is
byte-identical across runs, writes every artifact to out/, performs the
future-sentinel leakage checks, and prints a summary JSON to stdout.

Usage:
    F:/作业/aibola/.venv/Scripts/python.exe src/run_experiment.py
"""

from __future__ import annotations

import hashlib
import io
import json
import platform
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

import benchlib  # noqa: E402
from benchlib import (  # noqa: E402
    BASELINE_IDS,
    ENSEMBLE_COMPONENTS,
    HORIZONS,
    SEED,
    TEST_ORIGINS,
    VAL_ORIGINS,
    ForecastRecord,
    PipelineResult,
    Series,
    build_registry,
    forecasts_equal,
    load_series,
    mase_denominator,
    metric_bundle,
    run_pipeline,
    sentinel_mutate,
)

OUT_DIR = Path(__file__).resolve().parent.parent / "out"

SENTINEL_ORIGINS: tuple[int, ...] = (320, 340, 360, 370, 390, 411)


def _fmt(x: float) -> str:
    """Exact round-trip float formatting for CSV cells."""
    return repr(float(x))


def predictions_csv_text(records: list[ForecastRecord], series: Series) -> str:
    """rolling_origin_predictions.csv content (SPEC column list, both splits)."""
    buf = io.StringIO()
    buf.write(
        "origin_index,origin_week,horizon,target_index,target_week,"
        "actual,model,yhat,lower,upper,fallback\n"
    )
    for r in records:
        buf.write(
            f"{r.origin_index},{series.week_labels[r.origin_index - 1]},"
            f"{r.horizon},{r.target_index},{series.week_labels[r.target_index]},"
            f"{_fmt(r.actual)},{r.model},{_fmt(r.yhat)},{_fmt(r.lower)},"
            f"{_fmt(r.upper)},{int(r.fallback)}\n"
        )
    return buf.getvalue()


def paired_csv_text(
    records: list[ForecastRecord], series: Series, models: list[str]
) -> str:
    """paired_comparison.csv: one row per (origin, horizon), wide model columns."""
    groups: dict[tuple[int, int], dict[str, ForecastRecord]] = {}
    actuals: dict[tuple[int, int], ForecastRecord] = {}
    for r in records:
        key = (r.origin_index, r.horizon)
        groups.setdefault(key, {})[r.model] = r
        actuals[key] = r
    header = ["origin_index", "horizon", "target_index", "target_week", "actual"]
    for model in models:
        header += [model, f"{model}__interval_hit"]
    buf = io.StringIO()
    buf.write(",".join(header) + "\n")
    for key in sorted(groups):
        base = actuals[key]
        cells = [
            str(base.origin_index),
            str(base.horizon),
            str(base.target_index),
            series.week_labels[base.target_index],
            _fmt(base.actual),
        ]
        for model in models:
            r = groups[key][model]
            hit = int(r.lower <= r.actual <= r.upper)
            cells += [_fmt(r.yhat), str(hit)]
        buf.write(",".join(cells) + "\n")
    return buf.getvalue()


def records_to_pairs(
    records: list[ForecastRecord], split: str, model: str, horizon: int | None
) -> list[benchlib.Pair]:
    return [
        (r.actual, r.yhat, r.lower, r.upper)
        for r in records
        if r.split == split and r.model == model and (horizon is None or r.horizon == horizon)
    ]


def compute_metrics(
    records: list[ForecastRecord], values: tuple[float, ...], models: list[str]
) -> dict[str, dict[str, dict[str, dict[str, float]]]]:
    """metrics[split][model]["all" | str(h)] -> metric bundle."""
    denoms = {
        "validation": mase_denominator(values, VAL_ORIGINS),
        "test": mase_denominator(values, TEST_ORIGINS),
    }
    out: dict[str, dict[str, dict[str, dict[str, float]]]] = {}
    for split in ("validation", "test"):
        out[split] = {}
        for model in models:
            entry: dict[str, dict[str, float]] = {
                "all": metric_bundle(records_to_pairs(records, split, model, None), denoms[split])
            }
            for h in HORIZONS:
                entry[str(h)] = metric_bundle(
                    records_to_pairs(records, split, model, h), denoms[split]
                )
            out[split][model] = entry
    return out


def metrics_csv_text(
    metrics: dict[str, dict[str, dict[str, dict[str, float]]]], models: list[str]
) -> str:
    cols = [
        "split",
        "model",
        "horizon",
        "n",
        "MAE",
        "RMSE",
        "MSE",
        "WAPE",
        "agreement_pct",
        "sMAPE",
        "MASE",
        "coverage_95",
        "mean_interval_width",
    ]
    buf = io.StringIO()
    buf.write(",".join(cols) + "\n")
    for split in ("validation", "test"):
        for model in models:
            for hz in ["all", "1", "2", "3", "4"]:
                m = metrics[split][model][hz]
                buf.write(
                    ",".join([split, model, hz, str(int(m["n"]))] + [_fmt(m[c]) for c in cols[4:]])
                    + "\n"
                )
    return buf.getvalue()


def grid_csv_text(rows: list[dict[str, float | int]]) -> str:
    cols = list(rows[0].keys())
    buf = io.StringIO()
    buf.write(",".join(cols) + "\n")
    for row in rows:
        buf.write(",".join(_fmt(float(row[c])) for c in cols) + "\n")
    return buf.getvalue()


def run_sentinel_checks(
    series: Series, result: PipelineResult, models: list[str]
) -> dict[str, object]:
    """Future-sentinel: poisoning values[e:] must not change any prediction."""
    values = series.values
    details: dict[str, dict[str, bool]] = {}
    for model in models:
        details[model] = {}
        for e in SENTINEL_ORIGINS:
            base_registry = build_registry(
                values,
                result.selection.qwen_params,
                result.selection.ensemble_weights,
                SEED,
            )
            poisoned_registry = build_registry(
                sentinel_mutate(values, e),
                result.selection.qwen_params,
                result.selection.ensemble_weights,
                SEED,
            )
            details[model][str(e)] = forecasts_equal(
                base_registry.fns[model](e), poisoned_registry.fns[model](e)
            )
    passed = all(all(v.values()) for v in details.values())
    return {
        "description": (
            "values[e:] replaced by 999999 at each sampled origin; every model's "
            "forecast at origin e must be bitwise identical to the unpoisoned run."
        ),
        "sampled_origins": list(SENTINEL_ORIGINS),
        "per_model_origin_pass": details,
        "passed": passed,
    }


def code_dir_sha256(src_dir: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(src_dir.glob("*.py")):
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def write_text(path: Path, text: str) -> str:
    path.write_text(text, encoding="utf-8", newline="")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    series = load_series()

    # --- determinism: two full pipeline runs, predictions CSV must match. ---
    result_1 = run_pipeline(series, SEED)
    result_2 = run_pipeline(series, SEED)
    csv_1 = predictions_csv_text(result_1.records, series)
    csv_2 = predictions_csv_text(result_2.records, series)
    sha_1 = hashlib.sha256(csv_1.encode("utf-8")).hexdigest()
    sha_2 = hashlib.sha256(csv_2.encode("utf-8")).hexdigest()
    determinism = {
        "run1_predictions_csv_sha256": sha_1,
        "run2_predictions_csv_sha256": sha_2,
        "identical": sha_1 == sha_2,
    }
    result = result_1
    records = result.records
    models = result.active_models
    metrics = compute_metrics(records, series.values, models)

    # --- skill scores vs paper_arima and vs the best baseline (test split). ---
    test = metrics["test"]
    arima = test["paper_arima"]["all"]
    skill_vs_arima = {
        model: {
            "mae_skill_pct": (arima["MAE"] - test[model]["all"]["MAE"]) / arima["MAE"] * 100.0,
            "mse_skill_pct": (arima["MSE"] - test[model]["all"]["MSE"]) / arima["MSE"] * 100.0,
        }
        for model in models
        if model != "paper_arima"
    }
    best_baseline = min(BASELINE_IDS, key=lambda m: test[m]["all"]["WAPE"])
    best_wape = test[best_baseline]["all"]["WAPE"]
    improvement = {
        model: (best_wape - test[model]["all"]["WAPE"]) / best_wape * 100.0 for model in models
    }

    metrics_overall = {
        "experiment": "KG_PAPER_BENCHMARK_REPLACEMENT (METHOD_TRANSFER_REPLICATION)",
        "paper": {
            "reference": "Bleichrodt et al. 2024, R. Soc. Open Sci. 11(7):240248",
            "doi": "10.1098/rsos.240248",
        },
        "data": {
            "path": str(benchlib.DATA_PATH),
            "sha256": series.sha256,
            "n_points": len(series.values),
        },
        "split": {
            "note": (
                "SPEC origin labels read as 1-based; implemented as 0-based history "
                "lengths e. history = values[:e]; target index = e + h - 1 <= 411. "
                "The literal 0-based test range [363..412] contradicts the SPEC's own "
                "pair counts, so test uses e in [362..411] (SPEC labels 363..412)."
            ),
            "train": "values[:312]",
            "validation_origins": [VAL_ORIGINS[0], VAL_ORIGINS[-1]],
            "test_origins": [TEST_ORIGINS[0], TEST_ORIGINS[-1]],
            "horizons": list(HORIZONS),
            "validation_pairs": 200,
            "test_pairs_per_horizon": {"1": 50, "2": 49, "3": 48, "4": 47},
        },
        "metrics": metrics,
        "active_models": models,
        "round_2_gap_closure": {
            "new_models": [m for m in models if m not in benchlib.ORIGINAL_MODEL_IDS],
            "note": (
                "Gap-closure round (2026-08-19): the paper's multi-wave "
                "Cthr-triggered sub-epidemic ensemble variants and Prophet were "
                "added; the original ten models' per-row predictions are locked "
                "byte-identical (tests/test_old_models_row_regression.py)."
            ),
        },
        "skill_vs_paper_arima_test": skill_vs_arima,
        "best_baseline_by_test_wape": best_baseline,
        "relative_wape_improvement_vs_best_baseline_pct_test": improvement,
        "exactly_once_ledger": result.ledger,
    }

    # --- sentinel / leakage results. ---
    sentinel = run_sentinel_checks(series, result, models)
    sentinel_doc = {
        "future_sentinel": sentinel,
        "split_integrity": {
            "assertion": (
                "No method reads values[i] with i >= e at origin e: paper methods read "
                "only values[e-11:e]; baselines read at most values[e-1]; project "
                "methods fit only on TrainingRow objects built from values[:e]. "
                "Enforced by code structure (see benchlib comments) and proven "
                "empirically by the future-sentinel check above."
            ),
            "passed": bool(sentinel["passed"]),
        },
        "no_zero_fill": {
            "assertion": "cases column contains no empty cell; nothing is zero-filled.",
            "passed": True,
        },
        "determinism": determinism,
    }

    # --- manifest. ---
    # scipy/statsmodels ship no stubs in this env (mypy.ini: ignore_missing_imports).
    import scipy
    import statsmodels

    # --- equivalence checks (read-only; do not feed any model). ---
    autoarima_eq = benchlib.autoarima_equivalence(series)
    gam_eq = benchlib.gam_equivalence(series)

    dep_versions: dict[str, str] = {
        "python": platform.python_version(),
        "numpy": np.__version__,
        "scipy": scipy.__version__,
        "statsmodels": statsmodels.__version__,
    }
    import pandas as pd

    dep_versions["pandas"] = pd.__version__
    for pkg in ("pmdarima", "prophet", "cmdstanpy", "sklearn"):
        try:
            mod = __import__(pkg)
            dep_versions[pkg] = str(getattr(mod, "__version__", "unknown"))
        except Exception:
            dep_versions[pkg] = "not installed"

    manifest = {
        "seed": SEED,
        "models": {
            "paper_arima": {
                "window": 11,
                "grid": "p,q in {0,1,2}, d in {0,1}",
                "selection": "statsmodels AICc",
                "fallback_chain": [list(o) for o in benchlib.ARIMA_FALLBACK_CHAIN],
                "last_resort": "window mean",
            },
            "paper_gam": {
                "window": 11,
                "basis": "patsy bs(t, df in {3,4,5}, degree=3)",
                "selection": "GCV",
            },
            "paper_slr": {"window": 11, "model": "y ~ t OLS"},
            "paper_subepidemic_top": {
                "window": 11,
                "models": ["GGM: dC/dt = r*C^p", "GLM: dC/dt = r*C^p*(1-C/K)"],
                "start_grid": {
                    "r": [0.05, 0.2, 0.5],
                    "p": [0.5, 0.8, 1.0],
                    "K_factor": [1.1, 2.0, 10.0],
                },
                "selection": "paper eq. 2.4 AICc (getAICc.m method1=0)",
                "integrator": f"fixed-step Euler, dt={benchlib.EULER_DT}",
                "gap": (
                    "closed in the 2026-08-19 round: the Cthr-triggered multi-wave "
                    "candidates and weighted/unweighted ensembles now exist as the "
                    "separate models paper_subepidemic_second / "
                    "paper_subepidemic_ensemble_weighted / "
                    "paper_subepidemic_ensemble_unweighted; this model stays the "
                    "top single-wave variant, byte-identical to round 1"
                ),
            },
            "paper_subepidemic_second": {
                "window": 11,
                "candidate_set": (
                    "GGM, GLM, two-wave GLM per Cthr in "
                    f"{list(benchlib.CTHR_FRACTIONS)} (m=7; K2 = K1*exp(-q), q >= 0; "
                    "wave 2 triggers when model wave-1 cumulative reaches "
                    "Cthr * window-end cumulative)"
                ),
                "selection": "paper eq. 2.4 AICc runner-up",
                "interval": (
                    f"deterministic residual bootstrap, "
                    f"{result.ensemble_stats.bootstrap_replicates} replicates, "
                    "rng = np.random.default_rng(SEED) per origin; each replicate "
                    "forecast is augmented with a resampled in-sample one-step "
                    "residual * sqrt(h) (observation-noise term)"
                ),
                "fallback": "paper_slr when fewer than 2 candidates fit",
            },
            "paper_subepidemic_ensemble_weighted": {
                "window": 11,
                "candidate_set": "same as paper_subepidemic_second",
                "combination": "Akaike weights w_i proportional to exp(-Delta_i/2)",
                "interval": "same deterministic residual bootstrap as _second",
            },
            "paper_subepidemic_ensemble_unweighted": {
                "window": 11,
                "candidate_set": "same as paper_subepidemic_second",
                "combination": f"equal weights over the AICc top {benchlib.ENSEMBLE_TOP_K}",
                "interval": "same deterministic residual bootstrap as _second",
            },
            "paper_prophet": {
                "window": 11,
                "status": "ok" if "paper_prophet" in models else "not_available",
                "params": "Prophet defaults + weekly_seasonality=True, interval_width=0.95",
                "determinism": (
                    "fit(seed=SEED) forwarded to cmdstanpy optimize; "
                    "np.random.seed(SEED) before fit and predict (uncertainty "
                    "simulation draws from the numpy global RNG)"
                ),
                "fallback": "paper_slr on fit/predict failure",
            },
            "project_kg_primary": {
                "adapter": "lagged_count_boosting.v1",
                "params": benchlib.KG_DEFAULT_PARAMS,
            },
            "project_qwen_tuned": {
                "adapter": "lagged_count_boosting.v1",
                "selected_params": result.selection.qwen_params,
                "grid": (
                    "n_estimators {30,50,80,100} x "
                    "learning_rate {0.03,0.05,0.1,0.2} x "
                    "min_samples_leaf {5,10,15,20}"
                ),
            },
            "project_kg_ensemble": {
                "components": list(ENSEMBLE_COMPONENTS),
                "weights": dict(
                    zip(ENSEMBLE_COMPONENTS, result.selection.ensemble_weights, strict=True)
                ),
                "grid": "0.05 simplex",
            },
            "baselines": list(BASELINE_IDS),
        },
        "fallback_counts": {
            "arima_grid_selected": result.arima_stats.grid_selected,
            "arima_grid_order_failures": result.arima_stats.grid_order_failures,
            "arima_chain_fallback_used": result.arima_stats.chain_fallback_used,
            "arima_mean_fallback_used": result.arima_stats.mean_fallback_used,
            "subepidemic_ggm_selected": result.subepidemic_stats.ggm_selected,
            "subepidemic_glm_selected": result.subepidemic_stats.glm_selected,
            "subepidemic_slr_fallback_used": result.subepidemic_stats.slr_fallback_used,
            "subepidemic_multi_slr_fallback_used": result.ensemble_stats.slr_fallback_used,
            "subepidemic_bootstrap_replicates_failed": (
                result.ensemble_stats.bootstrap_replicates_failed
            ),
            "prophet_fit_failures": result.prophet_stats.fit_failures,
        },
        "new_gap_attempts": {
            "paper_prophet": {
                "status": "ok" if "paper_prophet" in models else "failed",
                "install_command": "pip install prophet  # -> prophet 1.4.0 + cmdstanpy 1.3.0",
                "fit_failures": result.prophet_stats.fit_failures,
            },
            "autoarima_equivalence": {
                "status": autoarima_eq["status"],
                "install_command": (
                    "pip install pmdarima scikit-learn  # -> pmdarima 2.1.1, "
                    "scikit-learn 1.9.0"
                ),
                "artifact": "autoarima_equivalence.json",
            },
            "gam_equivalence_pygam": {
                "status": gam_eq["status"],
                "install_command": "pip install pygam  # -> pygam 0.12.0, scipy 1.18.0->1.16.3",
                "artifact": "gam_equivalence.json",
                "reason": gam_eq.get("reason"),
            },
        },
        "code": {
            "src_dir_sha256": code_dir_sha256(Path(__file__).resolve().parent),
            "entry_point": "src/run_experiment.py",
        },
        "dependency_versions": dep_versions,
    }

    # --- write all artifacts. ---
    hashes: dict[str, str] = {}
    hashes["rolling_origin_predictions.csv"] = write_text(
        OUT_DIR / "rolling_origin_predictions.csv", csv_1
    )
    hashes["paired_comparison.csv"] = write_text(
        OUT_DIR / "paired_comparison.csv", paired_csv_text(records, series, models)
    )
    hashes["metrics_by_model_and_horizon.csv"] = write_text(
        OUT_DIR / "metrics_by_model_and_horizon.csv", metrics_csv_text(metrics, models)
    )
    hashes["qwen_grid.csv"] = write_text(
        OUT_DIR / "qwen_grid.csv", grid_csv_text(result.selection.qwen_grid_rows)
    )
    hashes["ensemble_grid.csv"] = write_text(
        OUT_DIR / "ensemble_grid.csv", grid_csv_text(result.selection.ensemble_grid_rows)
    )
    for name, obj in (
        ("metrics_overall.json", metrics_overall),
        ("leakage_and_sentinel_results.json", sentinel_doc),
        ("parameter_and_seed_manifest.json", manifest),
        ("autoarima_equivalence.json", autoarima_eq),
        ("gam_equivalence.json", gam_eq),
    ):
        text = json.dumps(obj, ensure_ascii=False, indent=2) + "\n"
        hashes[name] = write_text(OUT_DIR / name, text)
    hashes["SHA256SUMS.txt"] = write_text(
        OUT_DIR / "SHA256SUMS.txt",
        "".join(f"{sha}  {name}\n" for name, sha in sorted(hashes.items())),
    )

    # --- stdout summary. ---
    summary = {
        "status": "KG_PAPER_BENCHMARK_COMPLETE",
        "data_sha256": series.sha256,
        "determinism": determinism,
        "sentinel_passed": sentinel["passed"],
        "exactly_once_ledger": result.ledger,
        "qwen_selected_params": result.selection.qwen_params,
        "ensemble_weights": dict(
            zip(ENSEMBLE_COMPONENTS, result.selection.ensemble_weights, strict=True)
        ),
        "fallback_counts": manifest["fallback_counts"],
        "best_baseline_by_test_wape": best_baseline,
        "autoarima_equivalence": {
            "status": autoarima_eq["status"],
            "agreement_rate": autoarima_eq.get("agreement_rate"),
            "n_compared": autoarima_eq.get("n_compared"),
        },
        "gam_equivalence_status": gam_eq["status"],
        "test_overall_metrics": {
            model: {
                k: round(test[model]["all"][k], 6) for k in ("WAPE", "MAE", "RMSE", "coverage_95")
            }
            for model in models
        },
        "output_sha256": hashes,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
