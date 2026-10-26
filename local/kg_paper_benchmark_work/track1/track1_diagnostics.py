"""Track 1 diagnostics: root-cause analysis of the ARIMA and SE-Ranked(1) gaps.

Two questions, answered on the same 18 origins x 4 horizons as the main run
(calibration-10, locations World/Brazil/US(OWID)):

A. ARIMA: is the gap explained by benchlib's smaller search space
   (p,q in {0,1,2}, d in {0,1}, no drift) versus forecast::auto.arima's
   defaults (max.d=2, allowdrift=TRUE)?  We fit an extended statsmodels grid
   (d in {0,1,2}, plus trend='t' drift variants for d=1), select by AICc, and
   check whether the AICc-best extended model reproduces the official point
   forecasts, and which (order, drift) combination matches best.

B. SE-Ranked(1): how much of the gap is (i) benchlib ranking {GGM, GLM} when
   the official candidate set is GLM-only (paper: every sub-epidemic is a
   three-parameter generalized logistic growth model), (ii) parameter bounds
   (official: r in [mean(|d1:2|)/200, max(|d1:2|)*50], p in [0,1],
   K in [20, 1e10]; benchlib: r <= 5, p <= 3), and (iii) multi-wave official
   top models?  We fit single-wave GLM with official-style bounds and the
   official start (r=0.1, p=0.9, K=sum(window)) plus the benchlib start grid,
   and also score benchlib's full candidate set (incl. two-wave GLM2) top-1.

Output: track1/track1_diagnostics.json
"""

from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

import numpy as np

REPO = Path("F:/作业/aibola/kg_paper_benchmark_work")
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "track1"))

import benchlib  # noqa: E402
from run_track1_fidelity import (  # noqa: E402
    COMPARE_LOCATIONS,
    ORIGINS_BY_LOCATION,
    calibration_window,
    official_forecast,
    origin_to_date,
    reconstruct_weekly,
)

HORIZONS = (1, 2, 3, 4)
MATCH_RTOL = 1e-3  # R vs statsmodels/MATLAB optimizer numeric latitude

ARIMA_GRID_EXT = tuple((p, d, q) for p in (0, 1, 2) for d in (0, 1, 2) for q in (0, 1, 2))


def extended_arima_forecasts(window: tuple[float, ...]) -> list[dict]:
    """AICc-ranked extended-grid ARIMA fits; each entry carries its forecast."""
    from statsmodels.tsa.arima.model import ARIMA

    y = np.asarray(window, dtype=float)
    fits: list[dict] = []
    for order in ARIMA_GRID_EXT:
        trends = ["n"] if order[1] == 2 else (["c"] if order[1] == 0 else ["n", "t"])
        for trend in trends:
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    res = ARIMA(y, order=order, trend=trend).fit()
                    mean = np.asarray(res.get_forecast(4).predicted_mean, dtype=float)
                aicc = float(res.aicc)
            except Exception:
                continue
            if not (np.isfinite(aicc) and np.all(np.isfinite(mean))):
                continue
            fits.append(
                {
                    "order": order,
                    "drift": trend == "t",
                    "aicc": aicc,
                    "forecast": np.maximum(mean, 0.0),
                }
            )
    fits.sort(key=lambda f: f["aicc"])
    return fits


def fit_official_bounds_glm(sub: np.ndarray) -> tuple[np.ndarray, float] | None:
    """Single-wave GLM, official-style bounds and starts, scipy least_squares."""
    from scipy.optimize import least_squares

    cum = np.cumsum(sub)
    n = len(sub)
    c0 = float(cum[0])
    t_obs = n - 1
    r_lb = float(np.mean(np.abs(sub[:2]))) / 200.0
    r_ub = float(np.max(np.abs(sub[:2]))) * 50.0
    k_ub = 1e10

    def resid(x: np.ndarray) -> np.ndarray:
        sim = benchlib._simulate_cumulative("glm", x, c0, t_obs)
        if sim is None:
            return np.full(t_obs, 1e9)
        diff: np.ndarray = np.asarray(sim[1:], dtype=float) - cum[1:]
        return diff

    # official start from fittingModifiedLogisticFunctionPatchABC.m
    starts = [(0.1, 0.9, float(cum[-1]))]
    starts += [
        (r, p, k * float(cum[-1])) for r, p in benchlib.GGM_STARTS for k in (1.1, 2.0, 10.0)
    ]
    best: tuple[np.ndarray, float] | None = None
    for r0, p0, k0 in starts:
        x0 = np.array(
            [min(max(r0, r_lb * 1.001), r_ub * 0.999), min(max(p0, 1e-6), 1.0 - 1e-6), k0]
        )
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                sol = least_squares(
                    resid, x0, bounds=([r_lb, 0.0, 20.0], [r_ub, 1.0, k_ub]), method="trf"
                )
        except Exception:
            continue
        sse = float(np.sum(resid(np.asarray(sol.x, dtype=float)) ** 2))
        if np.isfinite(sse) and (best is None or sse < best[1]):
            best = (np.asarray(sol.x, dtype=float), sse)
    return best


def glm_official_bounds_forecast(sub: np.ndarray) -> dict[int, float] | None:
    fitted = fit_official_bounds_glm(sub)
    if fitted is None:
        return None
    params, _sse = fitted
    n = len(sub)
    c0 = float(np.cumsum(sub)[0])
    sim = benchlib._simulate_cumulative("glm", params, c0, n - 1 + max(HORIZONS))
    if sim is None:
        return None
    return {
        h: float(benchlib._clip0(sim[n - 1 + h] - sim[n - 2 + h])) for h in HORIZONS
    }


def rel(a: float, b: float) -> float:
    return abs(a - b) / max(abs(b), 1.0)


def main() -> None:
    wide = reconstruct_weekly()
    arima_rows: list[dict] = []
    se_rows: list[dict] = []
    for location in COMPARE_LOCATIONS:
        for origin in ORIGINS_BY_LOCATION[location]:
            origin_date = origin_to_date(origin)
            window = calibration_window(wide, location, origin_date, 10)

            # --- A: extended-grid ARIMA ---
            fits = extended_arima_forecasts(window)
            official = [official_forecast("ARIMA", 10, h, location, origin) for h in HORIZONS]
            best_fit = None
            best_rel = np.inf
            for f in fits:
                fc = [float(f["forecast"][h - 1]) for h in HORIZONS]
                r = float(np.mean([rel(fc[h - 1], official[h - 1]) for h in HORIZONS]))
                if r < best_rel:
                    best_rel = r
                    best_fit = f
            aicc_best = fits[0] if fits else None
            aicc_best_rel = None
            if aicc_best:
                fc = [float(aicc_best["forecast"][h - 1]) for h in HORIZONS]
                aicc_best_rel = float(np.mean([rel(fc[h - 1], official[h - 1]) for h in HORIZONS]))
            arima_rows.append(
                {
                    "location": location,
                    "origin": origin,
                    "official": official,
                    "aicc_best": {
                        "order": list(aicc_best["order"]),
                        "drift": aicc_best["drift"],
                        "aicc": aicc_best["aicc"],
                        "forecast": [float(x) for x in aicc_best["forecast"]],
                        "mean_rel_vs_official": aicc_best_rel,
                    }
                    if aicc_best
                    else None,
                    "closest": {
                        "order": list(best_fit["order"]),
                        "drift": best_fit["drift"],
                        "aicc": best_fit["aicc"],
                        "forecast": [float(x) for x in best_fit["forecast"]],
                        "mean_rel_vs_official": best_rel,
                    }
                    if best_fit
                    else None,
                }
            )

            # --- B: single-wave GLM with official bounds + full candidate set ---
            # Input alignment for the official US label swap (discrepancy D6).
            se_location = "US(CDC)" if location == "US(OWID)" else location
            se_window = calibration_window(wide, se_location, origin_date, 10)
            w = np.asarray(se_window, dtype=float)
            nz = np.nonzero(w > 0.0)[0]
            sub = w[int(nz[0]):] if len(nz) else None
            official_se = [
                official_forecast("SE-Ranked(1)", 10, h, location, origin) for h in HORIZONS
            ]
            row: dict = {"location": location, "origin": origin, "official": official_se}
            if sub is not None:
                glm_fc = glm_official_bounds_forecast(sub)
                if glm_fc is not None:
                    row["glm_official_bounds"] = {
                        "forecast": [glm_fc[h] for h in HORIZONS],
                        "mean_rel_vs_official": float(
                            np.mean([rel(glm_fc[h], official_se[h - 1]) for h in HORIZONS])
                        ),
                    }
                candidates = benchlib._fit_candidates(sub)
                if candidates:
                    top = candidates[0]
                    n = len(sub)
                    top_fc = [float(top.forecast_incidence(n, h)) for h in HORIZONS]
                    row["benchlib_full_candidate_top"] = {
                        "kind": top.kind,
                        "cthr": top.cthr,
                        "aicc": top.aicc,
                        "forecast": top_fc,
                        "mean_rel_vs_official": float(
                            np.mean([rel(top_fc[h - 1], official_se[h - 1]) for h in HORIZONS])
                        ),
                    }
            se_rows.append(row)

    def summarize(rows: list[dict], key: str) -> dict:
        vals = [r[key]["mean_rel_vs_official"] for r in rows if r.get(key)]
        arr = np.asarray(vals)
        return {
            "n": len(vals),
            "median_mean_rel": float(np.median(arr)),
            "max_mean_rel": float(arr.max()),
            "frac_exact_1e-3": float(np.mean(arr < MATCH_RTOL)),
        }

    payload = {
        "arima_extended_grid": {
            "grid": "p,q in {0,1,2} x d in {0,1,2}; d=1 also with trend='t' (drift)",
            "summary_aicc_best": summarize(
                [{ "aicc_best": r["aicc_best"]} for r in arima_rows], "aicc_best"
            ),
            "summary_closest": summarize(
                [{ "closest": r["closest"]} for r in arima_rows], "closest"
            ),
            "rows": arima_rows,
        },
        "se_ranked1_diagnostics": {
            "summary_glm_official_bounds": summarize(se_rows, "glm_official_bounds"),
            "summary_benchlib_full_candidate_top": summarize(
                se_rows, "benchlib_full_candidate_top"
            ),
            "rows": se_rows,
        },
    }
    out = REPO / "track1" / "track1_diagnostics.json"
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(payload["arima_extended_grid"]["summary_aicc_best"], indent=2))
    print(json.dumps(payload["arima_extended_grid"]["summary_closest"], indent=2))
    print(json.dumps(payload["se_ranked1_diagnostics"]["summary_glm_official_bounds"], indent=2))
    print(
        json.dumps(
            payload["se_ranked1_diagnostics"]["summary_benchlib_full_candidate_top"], indent=2
        )
    )


if __name__ == "__main__":
    main()
