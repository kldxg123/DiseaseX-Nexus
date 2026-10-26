"""Core library for the KG paper-benchmark rolling-origin experiment.

Deterministic comparison of the transferable univariate methods from
Bleichrodt et al. 2024 (R. Soc. Open Sci. 11(7):240248, DOI 10.1098/rsos.240248)
against the project methods, on the paper-aligned Singapore weekly dengue
series, under one shared rolling-origin protocol (see EXPERIMENT_SPEC.md).

Split convention
----------------
EXPERIMENT_SPEC.md writes validation origins "e in [313..362]" and test origins
"e in [363..412]" together with "history = values[:e]" and target index
"t_idx = e + h - 1 <= 411" (values indexed 0..411).  Read as 0-based history
lengths, the stated test range contradicts the SPEC's own pair counts
(h=1:50, h=2:49, h=3:48, h=4:47) and index 412 does not exist.  The SPEC
origin labels are therefore interpreted as 1-based labels and implemented as
0-based history lengths:

  validation origins e in [312, 361]  (SPEC labels 313..362), history values[:e]
  test origins       e in [362, 411]  (SPEC labels 363..412), history values[:e]

This reproduces every SPEC checkable constraint exactly: train = values[:312],
validation 50 origins x 4 horizons = 200 pairs (all valid), test pairs
h=1:50 / h=2:49 / h=3:48 / h=4:47, and it matches the paper's 311(+1)/50/50
split and the earlier project experiment (remote_edit/run_2024_method_comparison.py,
val = values[312:362], test = values[362:412]).
"""

from __future__ import annotations

import csv
import hashlib
import io
import math
import sys
import warnings
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np

# --- project adapter access (kept at the boundary, per AGENTS.md) ------------
KG_QWEN_SRC = Path("F:/作业/aibola/kg_qwen_work/src")
if str(KG_QWEN_SRC) not in sys.path:
    sys.path.insert(0, str(KG_QWEN_SRC))

# epidemic_core ships no py.typed marker; mypy reaches it via mypy_path with
# follow_imports=skip (see mypy.ini), so the adapter boundary stays untyped.
from epidemic_core.kg_qwen.adapters_ext import (  # noqa: E402
    fit_lagged_count_boosting,
    predict_lagged_count_boosting,
)
from epidemic_core.kg_r3c.execution import TrainingRow  # noqa: E402

# --- protocol constants -------------------------------------------------------
DATA_PATH = Path(
    "F:/作业/aibola/remote_edit/post2024_exact_inputs/"
    "singapore_dengue_official_paper_aligned_2012_2019.csv"
)
EXPECTED_SHA256 = "5efbff843186392c86862361a47c9fb15f30090d0a379b46f9e44267060afac2"
EXPECTED_N = 412
HORIZONS: tuple[int, ...] = (1, 2, 3, 4)
VAL_ORIGINS: tuple[int, ...] = tuple(range(312, 362))
TEST_ORIGINS: tuple[int, ...] = tuple(range(362, 412))
PAPER_WINDOW = 11
SEED = 20260819
START_DATE = date(2012, 1, 2)
EULER_DT = 0.05

MODEL_IDS: tuple[str, ...] = (
    "paper_arima",
    "paper_gam",
    "paper_slr",
    "paper_subepidemic_top",
    "project_kg_primary",
    "project_qwen_tuned",
    "project_kg_ensemble",
    "last_value",
    "seasonal_naive",
    "rolling_mean_4",
    # gap-closure round (2026-08-19): the paper's flagship multi-wave
    # sub-epidemic ensemble variants and Prophet.  Appended at the end so the
    # row/column order of the original ten models is untouched.
    "paper_subepidemic_second",
    "paper_subepidemic_ensemble_weighted",
    "paper_subepidemic_ensemble_unweighted",
    "paper_prophet",
)
# The ten models of the first round; their per-row predictions are locked
# byte-identical by tests/test_old_models_row_regression.py.
ORIGINAL_MODEL_IDS: tuple[str, ...] = MODEL_IDS[:10]
BASELINE_IDS: tuple[str, ...] = ("last_value", "seasonal_naive", "rolling_mean_4")

KG_DEFAULT_PARAMS: dict[str, float | int] = {
    "n_estimators": 50,
    "learning_rate": 0.1,
    "min_samples_leaf": 10,
}
QWEN_GRID: tuple[dict[str, float | int], ...] = tuple(
    {"n_estimators": ne, "learning_rate": lr, "min_samples_leaf": leaf}
    for ne in (30, 50, 80, 100)
    for lr in (0.03, 0.05, 0.1, 0.2)
    for leaf in (5, 10, 15, 20)
)
ENSEMBLE_COMPONENTS: tuple[str, ...] = (
    "project_qwen_tuned",
    "seasonal_naive",
    "last_value",
)
ENSEMBLE_GRID_STEP = 20  # weights on a 0.05 simplex grid

ARIMA_GRID: tuple[tuple[int, int, int], ...] = tuple(
    (p, d, q) for p in (0, 1, 2) for d in (0, 1) for q in (0, 1, 2)
)
ARIMA_FALLBACK_CHAIN: tuple[tuple[int, int, int], ...] = ((1, 1, 0), (0, 1, 1), (1, 0, 0))

GGM_STARTS: tuple[tuple[float, float], ...] = tuple(
    (r, p) for r in (0.05, 0.2, 0.5) for p in (0.5, 0.8, 1.0)
)
GLM_K_FACTORS: tuple[float, ...] = (1.1, 2.0, 10.0)

# --- multi-wave sub-epidemic ensemble constants (gap-closure round) -----------
CTHR_FRACTIONS: tuple[float, ...] = (0.30, 0.40, 0.50, 0.60, 0.70)
# Fixed deterministic start grid for the two-wave GLM: (r1, p1) reuse the
# single-wave GGM grid, r2 gets a small fixed pair, p2 and q start fixed.
TWO_WAVE_STARTS: tuple[tuple[float, float, float], ...] = tuple(
    (r1, p1, r2) for r1, p1 in GGM_STARTS for r2 in (0.1, 0.4)
)
TWO_WAVE_P2_START = 0.8
TWO_WAVE_Q_START = 0.25
# Deterministic residual bootstrap replicates per origin.  Protocol asks for
# 300; measured cost at 300 was ~105 min of refits for the 100 origins
# (~63 s/origin across the three ensemble models), far over the 20-minute
# budget, so the protocol-sanctioned reduction to 100 is applied and recorded
# in parameter_and_seed_manifest.json.
BOOTSTRAP_REPLICATES = 100
ENSEMBLE_TOP_K = 4


# --- data ---------------------------------------------------------------------
@dataclass(frozen=True)
class Series:
    """The verified weekly case series with ISO-ish week labels."""

    values: tuple[float, ...]
    week_labels: tuple[str, ...]
    sha256: str


def load_series(path: Path = DATA_PATH) -> Series:
    """Load the CSV, verifying sha256, length 412, and absence of missing weeks.

    The protocol forbids zero-filling missing weeks; the assertion that no
    ``cases`` cell is empty doubles as the no-zero-fill guarantee.
    """
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != EXPECTED_SHA256:
        raise RuntimeError(f"data sha256 mismatch: {digest} != {EXPECTED_SHA256}")
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8"))))
    # no_zero_fill assertion: no missing cases cell exists, so nothing is filled.
    assert all(r["cases"] not in ("", None) for r in rows), "missing cases cell found"
    values = tuple(float(r["cases"]) for r in rows)
    if len(values) != EXPECTED_N:
        raise RuntimeError(f"series must contain {EXPECTED_N} points, got {len(values)}")
    labels = tuple(f"{r['year']}-W{int(r['week']):02d}" for r in rows)
    return Series(values=values, week_labels=labels, sha256=digest)


# --- predictions --------------------------------------------------------------
@dataclass(frozen=True)
class Prediction:
    """One horizon forecast: point, 95% interval, and a fallback marker."""

    yhat: float
    lower: float
    upper: float
    fallback: bool = False


Forecast = dict[int, Prediction]
PredictFn = Callable[[int], Forecast]  # maps origin e -> forecast


def _clip0(x: float) -> float:
    return x if x > 0.0 else 0.0


def _residual_interval(yhat: float, h: int, q_lo: float, q_hi: float) -> tuple[float, float]:
    """Empirical residual quantile interval scaled by sqrt(h), truncated >= 0."""
    scale = math.sqrt(h)
    lo = _clip0(yhat + q_lo * scale)
    hi = _clip0(yhat + q_hi * scale)
    return lo, max(hi, lo)


def _quantiles_025_975(residuals: Sequence[float]) -> tuple[float, float]:
    arr = np.asarray(residuals, dtype=float)
    q_lo, q_hi = np.quantile(arr, [0.025, 0.975])
    return float(q_lo), float(q_hi)


# --- baselines ----------------------------------------------------------------
def _baseline_one_step(values: tuple[float, ...], model: str, i: int) -> float:
    """One-step prediction of a baseline at index i (uses only values[:i])."""
    if model == "last_value":
        return values[i - 1]
    if model == "seasonal_naive":
        return values[i - 52]
    if model == "rolling_mean_4":
        return float(np.mean(values[i - 4 : i]))
    raise ValueError(model)


def make_baseline_fn(values: tuple[float, ...], model: str) -> PredictFn:
    """Baseline point forecast plus a 95% PI from the last 52 one-step residuals.

    # split integrity: only indices < e are read (point: <= e-1; residuals: <= e-1).
    """

    def predict(e: int) -> Forecast:
        residuals = [values[i] - _baseline_one_step(values, model, i) for i in range(e - 52, e)]
        q_lo, q_hi = _quantiles_025_975(residuals)
        out: Forecast = {}
        for h in HORIZONS:
            if model == "last_value":
                yhat = values[e - 1]
            elif model == "seasonal_naive":
                yhat = values[e + h - 1 - 52]
            else:
                yhat = float(np.mean(values[e - 4 : e]))
            lo, hi = _residual_interval(yhat, h, q_lo, q_hi)
            out[h] = Prediction(yhat, lo, hi)
        return out

    return predict


# --- paper: SLR / GAM on the 11-point calibration window ----------------------
def _window(values: tuple[float, ...], e: int) -> tuple[float, ...]:
    # split integrity: the paper calibration window is values[e-11:e], all < e.
    return values[e - PAPER_WINDOW : e]


def _ols(y: np.ndarray, x_mat: np.ndarray) -> tuple[np.ndarray, float, int]:
    beta, _residuals, rank, _sv = np.linalg.lstsq(x_mat, y, rcond=None)
    rss = float(np.sum((y - x_mat @ beta) ** 2))
    return beta, rss, int(rank)


def _ols_forecast(beta: np.ndarray, rss: float, rank: int, n: int, x_pred: np.ndarray) -> Forecast:
    dof = max(n - rank, 1)
    sigma = math.sqrt(rss / dof)
    out: Forecast = {}
    point = x_pred @ beta
    for h in HORIZONS:
        yhat = _clip0(float(point[h - 1]))
        half = 1.96 * sigma * math.sqrt(h)
        out[h] = Prediction(yhat, _clip0(yhat - half), max(_clip0(yhat + half), 0.0))
    return out


def forecast_slr_window(window: Sequence[float]) -> Forecast:
    """Paper SLR: y ~ t OLS on the calibration window, t = 1..n, predict n+1..n+4.

    PI: yhat +/- 1.96 * sigma_resid * sqrt(h), truncated >= 0 (same form as GAM).
    """
    n = len(window)
    t = np.arange(1, n + 1, dtype=float)
    y = np.asarray(window, dtype=float)
    x_mat = np.column_stack([np.ones(n), t])
    beta, rss, rank = _ols(y, x_mat)
    t_pred = np.arange(n + 1, n + 5, dtype=float)
    x_pred = np.column_stack([np.ones(4), t_pred])
    return _ols_forecast(beta, rss, rank, n, x_pred)


def make_slr_fn(values: tuple[float, ...]) -> PredictFn:
    def predict(e: int) -> Forecast:
        return forecast_slr_window(_window(values, e))

    return predict


def forecast_gam_window(window: Sequence[float]) -> Forecast:
    """Paper GAM (deterministic equivalent): cubic B-spline basis + OLS.

    The paper uses mgcv penalized splines with GCV; per SPEC this is the
    deterministic equivalent: patsy ``bs(t, df=df, degree=3)`` + OLS over
    df in {3, 4, 5}, selected by GCV = n * RSS / (n - df_eff)^2.
    PI: yhat +/- 1.96 * sigma_resid * sqrt(h), truncated >= 0.

    patsy's ``bs`` refuses to evaluate outside the outermost knots, so the
    basis is fixed ex ante on the extended grid t = 1..n+4 (fit rows 1..n,
    prediction rows n+1..n+4 share the same knots); this is deterministic.
    """
    # patsy ships no py.typed marker (mypy.ini: ignore_missing_imports).
    import patsy

    n = len(window)
    t_all = np.arange(1, n + 5, dtype=float)
    y = np.asarray(window, dtype=float)
    best: tuple[float, int, np.ndarray, float, int] | None = None
    best_pred: np.ndarray | None = None
    for df in (3, 4, 5):
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                dmat = patsy.dmatrix(
                    f"bs(t, df={df}, degree=3, include_intercept=False)",
                    {"t": t_all},
                )
            x_full = np.asarray(dmat, dtype=float)
            beta, rss, rank = _ols(y, x_full[:n])
        except Exception:
            continue
        gcv = n * rss / max(n - rank, 1) ** 2
        if best is None or gcv < best[0]:
            best = (gcv, df, beta, rss, rank)
            best_pred = x_full[n:]
    if best is None or best_pred is None:
        return forecast_slr_window(window)
    _gcv, _df, beta, rss, rank = best
    return _ols_forecast(beta, rss, rank, n, best_pred)


def make_gam_fn(values: tuple[float, ...]) -> PredictFn:
    def predict(e: int) -> Forecast:
        return forecast_gam_window(_window(values, e))

    return predict


# --- paper: ARIMA on the 11-point calibration window --------------------------
@dataclass
class ArimaStats:
    """Fallback bookkeeping for paper_arima (written into the manifest)."""

    grid_selected: int = 0
    grid_order_failures: int = 0
    chain_fallback_used: int = 0
    mean_fallback_used: int = 0


def _fit_arima(window: Sequence[float], order: tuple[int, int, int]) -> tuple[Any, float]:
    """Fit one statsmodels ARIMA deterministically; raise on any failure.

    The paper uses R forecast::auto.arima stepwise search; per SPEC this is the
    deterministic equivalent: an exhaustive (p, d, q) grid selected by AICc
    (statsmodels ``.aicc``).
    """
    # statsmodels ships no py.typed marker (mypy.ini: ignore_missing_imports).
    from statsmodels.tsa.arima.model import ARIMA

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        result = ARIMA(np.asarray(window, dtype=float), order=order).fit()
    aicc = float(result.aicc)
    if not math.isfinite(aicc):
        raise ArithmeticError(f"non-finite AICc for order {order}")
    return result, aicc


def forecast_arima_window(window: Sequence[float], stats: ArimaStats) -> Forecast:
    """ARIMA grid + AICc selection; fallback chain; last resort: window mean."""
    best: tuple[float, Any] | None = None
    for order in ARIMA_GRID:
        try:
            result, aicc = _fit_arima(window, order)
        except Exception:
            stats.grid_order_failures += 1
            continue
        if best is None or aicc < best[0]:
            best = (aicc, result)
    if best is None:
        for order in ARIMA_FALLBACK_CHAIN:
            try:
                result, aicc = _fit_arima(window, order)
            except Exception:
                continue
            best = (aicc, result)
            stats.chain_fallback_used += 1
            break
    if best is None:
        stats.mean_fallback_used += 1
        mean = float(np.mean(np.asarray(window, dtype=float)))
        sigma = float(np.std(np.asarray(window, dtype=float), ddof=1))
        out: Forecast = {}
        for h in HORIZONS:
            half = 1.96 * sigma * math.sqrt(h)
            out[h] = Prediction(
                _clip0(mean),
                _clip0(mean - half),
                max(_clip0(mean + half), 0.0),
                fallback=True,
            )
        return out
    stats.grid_selected += 1
    result = best[1]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        fc = result.get_forecast(4)
        mean_arr = np.asarray(fc.predicted_mean, dtype=float)
        ci = np.asarray(fc.conf_int(alpha=0.05), dtype=float)
    out = {}
    for h in HORIZONS:
        yhat = _clip0(float(mean_arr[h - 1]))
        lo, hi = float(ci[h - 1, 0]), float(ci[h - 1, 1])
        lo = _clip0(lo) if math.isfinite(lo) else yhat
        hi = _clip0(hi) if math.isfinite(hi) else yhat
        out[h] = Prediction(yhat, lo, max(hi, lo))
    return out


def make_arima_fn(values: tuple[float, ...], stats: ArimaStats) -> PredictFn:
    def predict(e: int) -> Forecast:
        return forecast_arima_window(_window(values, e), stats)

    return predict


# --- paper: sub-epidemic top single-wave model (GGM / GLM) --------------------
@dataclass
class SubepidemicStats:
    """Bookkeeping for paper_subepidemic_top."""

    ggm_selected: int = 0
    glm_selected: int = 0
    slr_fallback_used: int = 0


def _simulate_cumulative(
    kind: str, params: Sequence[float] | np.ndarray, c0: float, t_max: int
) -> list[float] | None:
    """Fixed-step Euler integration of dC/dt (deterministic, dt = EULER_DT).

    GGM (flag1=0): dC/dt = r * C^p.
    GLM (flag1=1): dC/dt = r * C^p * (1 - C/K).
    Returns cumulative values at integer times 0..t_max, or None on blow-up.
    """
    steps_per_week = round(1.0 / EULER_DT)
    c = c0
    out = [c]
    r = float(params[0])
    p = float(params[1])
    k = float(params[2]) if kind == "glm" else 0.0
    for step in range(1, t_max * steps_per_week + 1):
        if c <= 0.0:
            rate = 0.0 if c == 0.0 else math.nan
        else:
            rate = r * c**p
            if kind == "glm":
                rate *= 1.0 - c / k
        c += rate * EULER_DT
        if not math.isfinite(c) or c > 1e12 or c < 0.0:
            return None
        if step % steps_per_week == 0:
            out.append(c)
    return out


def _paper_aicc(sse: float, n: int, m: int) -> float | None:
    """Paper eq. 2.4 / official getAICc.m (method1=0, dist1=0, fixI0=1).

    AICc = n*log(SSE) + 2m + 2m(m+1)/(n-m-1), with m = #params + 1 variance
    term: m = 3 for GGM (r, p), m = 4 for GLM (r, p, K).
    """
    if n - m - 1 <= 0:
        return None
    return n * math.log(max(sse, 1e-12)) + 2 * m + 2 * m * (m + 1) / (n - m - 1)


def _fit_wave(kind: str, sub: np.ndarray) -> tuple[np.ndarray, float] | None:
    """Multi-start nonlinear least squares on the cumulative curve."""
    # scipy is imported lazily so sentinel/determinism tests stay cheap.
    # (scipy has no stubs in this env; mypy.ini: ignore_missing_imports.)
    from scipy.optimize import least_squares

    cum = np.cumsum(sub)
    n = len(sub)
    c0 = float(cum[0])
    cmax = float(cum[-1])
    t_obs = n - 1

    def resid(x: np.ndarray) -> np.ndarray:
        sim = _simulate_cumulative(kind, x, c0, t_obs)
        if sim is None:
            return np.full(t_obs, 1e9)
        diff: np.ndarray = np.asarray(sim[1:], dtype=float) - cum[1:]
        return diff

    if kind == "ggm":
        starts = [np.array([r, p]) for r, p in GGM_STARTS]
        bounds = ([1e-8, 0.0], [5.0, 3.0])
    else:
        starts = [np.array([r, p, k * cmax]) for r, p in GGM_STARTS for k in GLM_K_FACTORS]
        bounds = ([1e-8, 0.0, 0.5 * cmax], [5.0, 3.0, 1e8])
    best: tuple[np.ndarray, float] | None = None
    for x0 in starts:
        try:
            sol = least_squares(resid, x0, bounds=bounds, method="trf")
        except Exception:
            continue
        sse = float(np.sum(resid(np.asarray(sol.x, dtype=float)) ** 2))
        if not math.isfinite(sse):
            continue
        if best is None or sse < best[1]:
            best = (np.asarray(sol.x, dtype=float), sse)
    return best


def forecast_subepidemic_window(window: Sequence[float], stats: SubepidemicStats) -> Forecast:
    """Top single-wave sub-epidemic model selected between {GGM, GLM} by AICc.

    The paper's full ensemble framework adds Cthr-triggered extra waves (n <= 5)
    and weighted/unweighted ensembles; per SPEC only the top single-wave model
    is implemented here and the multi-wave ensemble is a recorded gap.
    Leading zeros of the calibration window are truncated to the first non-zero
    observation (paper protocol).  Full failure falls back to paper_slr.
    """
    w = np.asarray(window, dtype=float)
    nonzero = np.nonzero(w > 0.0)[0]
    if len(nonzero) == 0:
        stats.slr_fallback_used += 1
        return _mark_fallback(forecast_slr_window(window))
    sub = w[int(nonzero[0]) :]
    n = len(sub)
    candidates: list[tuple[float, str, np.ndarray]] = []
    for kind, m in (("ggm", 3), ("glm", 4)):
        fitted = _fit_wave(kind, sub)
        if fitted is None:
            continue
        params, sse = fitted
        aicc = _paper_aicc(sse, n, m)
        if aicc is not None and math.isfinite(aicc):
            candidates.append((aicc, kind, params))
    if not candidates:
        stats.slr_fallback_used += 1
        return _mark_fallback(forecast_slr_window(window))
    candidates.sort(key=lambda c: (c[0], c[1]))  # tie -> "ggm" before "glm"
    _aicc, kind, params = candidates[0]
    if kind == "ggm":
        stats.ggm_selected += 1
    else:
        stats.glm_selected += 1
    c0 = float(np.cumsum(sub)[0])
    sim = _simulate_cumulative(kind, params, c0, n - 1 + max(HORIZONS))
    if sim is None:
        stats.slr_fallback_used += 1
        return _mark_fallback(forecast_slr_window(window))
    # In-sample one-step incidence residuals drive the empirical 95% PI.
    residuals = [float(sub[i]) - (sim[i] - sim[i - 1]) for i in range(1, n)]
    q_lo, q_hi = _quantiles_025_975(residuals)
    out: Forecast = {}
    for h in HORIZONS:
        yhat = _clip0(sim[n - 1 + h] - sim[n - 2 + h])
        lo, hi = _residual_interval(yhat, h, q_lo, q_hi)
        out[h] = Prediction(yhat, lo, hi)
    return out


def _mark_fallback(forecast: Forecast) -> Forecast:
    return {h: Prediction(p.yhat, p.lower, p.upper, fallback=True) for h, p in forecast.items()}


def make_subepidemic_fn(values: tuple[float, ...], stats: SubepidemicStats) -> PredictFn:
    def predict(e: int) -> Forecast:
        return forecast_subepidemic_window(_window(values, e), stats)

    return predict


# --- paper: multi-wave sub-epidemic ensembles (flagship gap closure) ----------
@dataclass
class WaveCandidate:
    """One fitted sub-epidemic candidate on a zero-truncated window."""

    kind: str  # "ggm" | "glm" | "glm2"
    cthr: float | None  # trigger fraction, only for kind == "glm2"
    params: np.ndarray
    sse: float
    aicc: float
    sim: list[float]  # cumulative at integer times 0 .. n-1+max(HORIZONS)

    def forecast_incidence(self, n: int, h: int) -> float:
        return _clip0(self.sim[n - 1 + h] - self.sim[n - 2 + h])

    def fitted_incidence(self, n: int) -> np.ndarray:
        """In-sample one-week model incidence at times 1..n-1."""
        return np.asarray([self.sim[i] - self.sim[i - 1] for i in range(1, n)], dtype=float)


@dataclass
class EnsembleSubepidemicStats:
    """Bookkeeping for the three multi-wave sub-epidemic models."""

    slr_fallback_used: dict[str, int] = field(
        default_factory=lambda: {
            "paper_subepidemic_second": 0,
            "paper_subepidemic_ensemble_weighted": 0,
            "paper_subepidemic_ensemble_unweighted": 0,
        }
    )
    bootstrap_replicates_failed: int = 0
    bootstrap_replicates: int = BOOTSTRAP_REPLICATES


def _simulate_two_wave_cumulative(
    params: Sequence[float] | np.ndarray, c0: float, t_max: int, onset_thr: float
) -> list[float] | None:
    """Fixed-step Euler integration of the Cthr-triggered two-wave GLM.

    Wave 1 is a GLM (r1, p1, K1) started at c0.  Wave 2 (r2, p2, K2) with
    K2 = K1 * exp(-q), q >= 0, activates at the first Euler sub-step where the
    model's own wave-1 cumulative reaches ``onset_thr``; its initial cumulative
    is the wave-1 level at the trigger time (the official code's ``Cinvasions``
    convention), so the total cumulative is c1 + (c2 - c2_at_trigger), which is
    continuous at the trigger.  Returns cumulative values at integer times
    0..t_max, or None on blow-up.
    """
    r1, p1, k1, r2, p2, q = (float(x) for x in params)
    k2 = k1 * math.exp(-q)
    steps_per_week = round(1.0 / EULER_DT)
    c1 = c0
    c2 = 0.0
    c2_anchor = 0.0
    active = False
    out = [c0]
    for step in range(1, t_max * steps_per_week + 1):
        if not active and c1 >= onset_thr:
            active = True
            c2 = c1
            c2_anchor = c1
        if c1 <= 0.0:
            rate1 = 0.0 if c1 == 0.0 else math.nan
        else:
            rate1 = r1 * c1**p1 * (1.0 - c1 / k1)
        c1 += rate1 * EULER_DT
        if active:
            rate2 = 0.0 if c2 <= 0.0 else r2 * c2**p2 * (1.0 - c2 / k2)
            c2 += rate2 * EULER_DT
        total = c1 + (c2 - c2_anchor) if active else c1
        if not math.isfinite(total) or total > 1e12 or c1 < 0.0:
            return None
        if step % steps_per_week == 0:
            out.append(total)
    return out


def _wave_simulate(
    kind: str, params: Sequence[float] | np.ndarray, c0: float, t_max: int, onset_thr: float
) -> list[float] | None:
    if kind == "glm2":
        return _simulate_two_wave_cumulative(params, c0, t_max, onset_thr)
    return _simulate_cumulative(kind, params, c0, t_max)


def _fit_two_wave(sub: np.ndarray, cthr: float) -> tuple[np.ndarray, float] | None:
    """Multi-start nonlinear least squares for the two-wave GLM on cumsum(sub).

    Parameter vector (r1, p1, K1, r2, p2, q); the start grid is the fixed
    TWO_WAVE_STARTS grid with K1_0 = 2 * (window-end cumulative) and
    q_0 = TWO_WAVE_Q_START.
    """
    from scipy.optimize import least_squares

    cum = np.cumsum(sub)
    n = len(sub)
    c0 = float(cum[0])
    c_end = float(cum[-1])
    onset_thr = cthr * c_end
    t_obs = n - 1

    def resid(x: np.ndarray) -> np.ndarray:
        sim = _simulate_two_wave_cumulative(x, c0, t_obs, onset_thr)
        if sim is None:
            return np.full(t_obs, 1e9)
        diff: np.ndarray = np.asarray(sim[1:], dtype=float) - cum[1:]
        return diff

    bounds = ([1e-8, 0.0, 0.5 * c_end, 1e-8, 0.0, 0.0], [5.0, 3.0, 1e8, 5.0, 3.0, 10.0])
    best: tuple[np.ndarray, float] | None = None
    for r1, p1, r2 in TWO_WAVE_STARTS:
        x0 = np.array([r1, p1, 2.0 * c_end, r2, TWO_WAVE_P2_START, TWO_WAVE_Q_START])
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                sol = least_squares(resid, x0, bounds=bounds, method="trf")
        except Exception:
            continue
        sse = float(np.sum(resid(np.asarray(sol.x, dtype=float)) ** 2))
        if not math.isfinite(sse):
            continue
        if best is None or sse < best[1]:
            best = (np.asarray(sol.x, dtype=float), sse)
    return best


def _fit_candidates(sub: np.ndarray) -> list[WaveCandidate]:
    """Fit the full candidate set and sort by paper AICc (eq. 2.4).

    Candidates: single-wave GGM (m=3), single-wave GLM (m=4), and one
    Cthr-triggered two-wave GLM (m=7) per fraction in CTHR_FRACTIONS.  The AICc
    correction needs n - m - 1 > 0, so two-wave candidates silently drop out of
    windows with fewer than 9 observations after leading-zero truncation.
    Sorting ties break by (kind, cthr) for full determinism.
    """
    cum = np.cumsum(sub)
    n = len(sub)
    c0 = float(cum[0])
    c_end = float(cum[-1])
    t_max = n - 1 + max(HORIZONS)
    specs: list[tuple[str, float | None, int]] = [("ggm", None, 3), ("glm", None, 4)]
    specs += [("glm2", c, 7) for c in CTHR_FRACTIONS]
    candidates: list[WaveCandidate] = []
    for kind, cthr, m in specs:
        onset_thr = cthr * c_end if cthr is not None else 0.0
        if kind == "glm2":
            assert cthr is not None
            fitted = _fit_two_wave(sub, cthr)
        else:
            fitted = _fit_wave(kind, sub)
        if fitted is None:
            continue
        params, sse = fitted
        aicc = _paper_aicc(sse, n, m)
        if aicc is None or not math.isfinite(aicc):
            continue
        sim = _wave_simulate(kind, params, c0, t_max, onset_thr)
        if sim is None:
            continue
        candidates.append(WaveCandidate(kind, cthr, params, sse, aicc, sim))
    candidates.sort(key=lambda c: (c.aicc, c.kind, c.cthr if c.cthr is not None else 0.0))
    return candidates


CandidateCacheEntry = tuple[tuple[float, ...], "np.ndarray | None", list[WaveCandidate]]
CandidateCache = Callable[[int], CandidateCacheEntry]


def make_candidate_cache(values: tuple[float, ...]) -> CandidateCache:
    """Memoized per-origin candidate fits shared by the three ensemble models.

    Each registry gets its own cache, so sentinel runs with poisoned series
    stay independent.  # split integrity: only values[e-11:e] is read.
    """
    cache: dict[int, CandidateCacheEntry] = {}

    def get(e: int) -> CandidateCacheEntry:
        if e not in cache:
            window = _window(values, e)
            w = np.asarray(window, dtype=float)
            nonzero = np.nonzero(w > 0.0)[0]
            if len(nonzero) == 0:
                cache[e] = (window, None, [])
            else:
                sub = w[int(nonzero[0]) :]
                cache[e] = (window, sub, _fit_candidates(sub))
        return cache[e]

    return get


def _aicc_weights(candidates: Sequence[WaveCandidate]) -> np.ndarray:
    """Akaike weights w_i proportional to exp(-Delta_i / 2)."""
    a = np.asarray([c.aicc for c in candidates], dtype=float)
    w = np.exp(-(a - a.min()) / 2.0)
    total = float(w.sum())
    result: np.ndarray = w / total
    return result


def _mode_members(
    mode: str, candidates: list[WaveCandidate]
) -> tuple[list[WaveCandidate], np.ndarray] | None:
    """Members and combination weights for one ensemble mode.

    "second": the AICc runner-up alone (None when fewer than 2 candidates).
    "unweighted": equal weights over the AICc top ENSEMBLE_TOP_K.
    "weighted": Akaike weights over all candidates.
    """
    if mode == "second":
        if len(candidates) < 2:
            return None
        return [candidates[1]], np.array([1.0])
    if mode == "unweighted":
        top = list(candidates[:ENSEMBLE_TOP_K])
        return top, np.full(len(top), 1.0 / len(top))
    return list(candidates), _aicc_weights(candidates)


def _combined_forecast_incidence(
    members: Sequence[WaveCandidate], weights: np.ndarray, n: int, h: int
) -> float:
    return float(
        sum(w * c.forecast_incidence(n, h) for w, c in zip(weights, members, strict=True))
    )


def _combined_fitted_incidence(
    members: Sequence[WaveCandidate], weights: np.ndarray, n: int
) -> np.ndarray:
    total = np.zeros(n - 1, dtype=float)
    for w, c in zip(weights, members, strict=True):
        total = total + w * c.fitted_incidence(n)
    return total


def _clip_into_bounds(x0: np.ndarray, lb: list[float], ub: list[float]) -> np.ndarray:
    lo = np.asarray(lb, dtype=float) + 1e-9
    hi = np.asarray(ub, dtype=float) - 1e-9
    clipped: np.ndarray = np.minimum(np.maximum(np.asarray(x0, dtype=float), lo), hi)
    return clipped


def _refit_candidate(cand: WaveCandidate, sub_b: np.ndarray) -> WaveCandidate | None:
    """Warm-start refit of one candidate structure on a resampled window.

    Single-start least_squares from the original fitted parameters (the
    bootstrap perturbation is small), same bounds and AICc rule as the base
    fit; returns None on any failure so the replicate can be skipped.
    """
    from scipy.optimize import least_squares

    cum = np.cumsum(sub_b)
    n = len(sub_b)
    c0 = float(cum[0])
    c_end = float(cum[-1])
    t_obs = n - 1
    onset_thr = cand.cthr * c_end if cand.cthr is not None else 0.0

    def resid(x: np.ndarray) -> np.ndarray:
        sim = _wave_simulate(cand.kind, x, c0, t_obs, onset_thr)
        if sim is None:
            return np.full(t_obs, 1e9)
        diff: np.ndarray = np.asarray(sim[1:], dtype=float) - cum[1:]
        return diff

    if cand.kind == "ggm":
        lb, ub, m = [1e-8, 0.0], [5.0, 3.0], 3
    elif cand.kind == "glm":
        lb, ub, m = [1e-8, 0.0, 0.5 * c_end], [5.0, 3.0, 1e8], 4
    else:
        lb = [1e-8, 0.0, 0.5 * c_end, 1e-8, 0.0, 0.0]
        ub = [5.0, 3.0, 1e8, 5.0, 3.0, 10.0]
        m = 7
    x0 = _clip_into_bounds(cand.params, lb, ub)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            sol = least_squares(resid, x0, bounds=(lb, ub), method="trf")
    except Exception:
        return None
    params = np.asarray(sol.x, dtype=float)
    sse = float(np.sum(resid(params) ** 2))
    if not math.isfinite(sse):
        return None
    aicc = _paper_aicc(sse, n, m)
    if aicc is None or not math.isfinite(aicc):
        return None
    sim = _wave_simulate(cand.kind, params, c0, n - 1 + max(HORIZONS), onset_thr)
    if sim is None:
        return None
    return WaveCandidate(cand.kind, cand.cthr, params, sse, aicc, sim)


def _bootstrap_intervals(
    mode: str,
    sub: np.ndarray,
    candidates: list[WaveCandidate],
    stats: EnsembleSubepidemicStats,
) -> dict[int, tuple[float, float]]:
    """Deterministic residual bootstrap 95% PIs (paper: parametric bootstrap).

    In-sample one-step incidence residuals of the model's combined fit are
    resampled with replacement (np.random.default_rng(SEED), freshly seeded per
    call so every origin sees the same stream), added to the fitted incidence
    (truncated >= 0), and the same candidate structures are warm-refit on each
    pseudo window.  For "weighted" the Akaike weights are recomputed per
    replicate; "second" and "unweighted" keep the original structure set
    (structure-conditional bootstrap).  Each replicate's h-step forecast is
    then augmented with one resampled in-sample residual times sqrt(h) -- the
    observation-noise term shared by the other models' PIs in this benchmark;
    without it the intervals reflect parameter uncertainty only and
    under-cover badly (validation coverage 0.27-0.37 vs 0.65-0.73 with it).
    The 2.5%/97.5% quantiles of the BOOTSTRAP_REPLICATES augmented forecasts,
    truncated >= 0, form the interval.
    """
    n = len(sub)
    members = _mode_members(mode, candidates)
    if members is None:
        raise ValueError(f"mode {mode} needs at least 2 candidates")
    member_list, weights = members
    fitted = _combined_fitted_incidence(member_list, weights, n)
    residuals = np.asarray(sub[1:], dtype=float) - fitted
    rng = np.random.default_rng(SEED)
    draws: dict[int, list[float]] = {h: [] for h in HORIZONS}
    for _b in range(BOOTSTRAP_REPLICATES):
        idx = rng.integers(0, n - 1, size=n - 1)
        sub_b = np.concatenate(([float(sub[0])], np.maximum(fitted + residuals[idx], 0.0)))
        refits: list[WaveCandidate] = []
        for cand in member_list:
            refit = _refit_candidate(cand, sub_b)
            if refit is None:
                break
            refits.append(refit)
        if len(refits) != len(member_list):
            stats.bootstrap_replicates_failed += 1
            continue
        w_b = _aicc_weights(refits) if mode == "weighted" else weights
        eps_idx = rng.integers(0, n - 1, size=max(HORIZONS))
        for h in HORIZONS:
            point = _combined_forecast_incidence(refits, w_b, n, h)
            noise = float(residuals[eps_idx[h - 1]]) * math.sqrt(h)
            draws[h].append(point + noise)
    out: dict[int, tuple[float, float]] = {}
    for h in HORIZONS:
        if draws[h]:
            lo, hi = np.quantile(np.asarray(draws[h], dtype=float), [0.025, 0.975])
            out[h] = (_clip0(float(lo)), _clip0(float(hi)))
        else:
            point = _clip0(_combined_forecast_incidence(member_list, weights, n, h))
            out[h] = (point, point)
    return out


def make_subepidemic_multi_fn(
    mode: str, get_candidates: CandidateCache, stats: EnsembleSubepidemicStats
) -> PredictFn:
    """PredictFn for the second / weighted / unweighted ensemble variants.

    mode is one of "second", "weighted", "unweighted".  All fits fail (or the
    AICc runner-up does not exist for "second") -> deterministic paper_slr
    fallback, flagged and counted.
    """
    model_id = (
        "paper_subepidemic_second" if mode == "second" else f"paper_subepidemic_ensemble_{mode}"
    )

    def predict(e: int) -> Forecast:
        window, sub, candidates = get_candidates(e)
        members = None if sub is None else _mode_members(mode, candidates)
        if sub is None or members is None:
            stats.slr_fallback_used[model_id] += 1
            return _mark_fallback(forecast_slr_window(window))
        member_list, weights = members
        n = len(sub)
        intervals = _bootstrap_intervals(mode, sub, candidates, stats)
        out: Forecast = {}
        for h in HORIZONS:
            yhat = _clip0(_combined_forecast_incidence(member_list, weights, n, h))
            lo, hi = intervals[h]
            out[h] = Prediction(yhat, lo, max(hi, lo))
        return out

    return predict


# --- paper: Prophet on the 11-point calibration window ------------------------
@dataclass
class ProphetStats:
    """Bookkeeping for paper_prophet."""

    fit_failures: int = 0


def prophet_available() -> bool:
    """True when the prophet package imports (optional dependency)."""
    try:
        import prophet  # noqa: F401
    except Exception:
        return False
    return True


def _forecast_prophet_window(e: int, window: Sequence[float]) -> Forecast:
    """paper_prophet: Prophet defaults + weekly seasonality on the 11-week
    calibration window, native 95% interval, truncated >= 0.

    Determinism: Prophet.fit forwards ``seed`` to cmdstanpy's optimize (fixed
    optimizer seed), and Prophet's uncertainty simulation draws from the numpy
    global RNG, which is re-seeded with SEED immediately before fit and
    predict.  Weekly dates end at the origin week ( Mondays, W-MON ), matching
    the series convention START_DATE + 7*i days.
    """
    import logging

    import pandas as pd
    from prophet import Prophet

    logging.getLogger("prophet").setLevel(logging.ERROR)
    logging.getLogger("cmdstanpy").setLevel(logging.ERROR)
    dates = [START_DATE + timedelta(weeks=e - PAPER_WINDOW + i) for i in range(PAPER_WINDOW)]
    df = pd.DataFrame({"ds": pd.to_datetime(dates), "y": np.asarray(window, dtype=float)})
    model = Prophet(weekly_seasonality=True, interval_width=0.95)
    np.random.seed(SEED)
    model.fit(df, seed=SEED)
    future = model.make_future_dataframe(periods=max(HORIZONS), freq="W-MON")
    np.random.seed(SEED)
    fc = model.predict(future)
    tail = fc.tail(max(HORIZONS))
    yhat = tail["yhat"].to_numpy(dtype=float)
    lo_arr = tail["yhat_lower"].to_numpy(dtype=float)
    hi_arr = tail["yhat_upper"].to_numpy(dtype=float)
    out: Forecast = {}
    for h in HORIZONS:
        y = _clip0(float(yhat[h - 1]))
        lo = _clip0(float(lo_arr[h - 1]))
        hi = _clip0(float(hi_arr[h - 1]))
        out[h] = Prediction(y, lo, max(hi, lo))
    return out


def make_prophet_fn(values: tuple[float, ...], stats: ProphetStats) -> PredictFn:
    def predict(e: int) -> Forecast:
        window = _window(values, e)
        try:
            return _forecast_prophet_window(e, window)
        except Exception:
            stats.fit_failures += 1
            return _mark_fallback(forecast_slr_window(window))

    return predict


# --- project: lagged_count_boosting.v1 via the kg_qwen adapter ----------------
def forecast_project(
    values: tuple[float, ...], e: int, params: dict[str, float | int], seed: int
) -> Forecast:
    """Project adapter fit on the full history values[:e]; recursive h=1..4.

    # split integrity: only values[:e] is turned into TrainingRow objects.
    """
    history = values[:e]
    rows = [
        TrainingRow(START_DATE + timedelta(weeks=i), None, v, f"official-{i + 1}")
        for i, v in enumerate(history)
    ]
    cutoff = rows[-1].period_start
    fit = fit_lagged_count_boosting(rows, params, seed, cutoff)
    raw = predict_lagged_count_boosting(fit, list(HORIZONS), cutoff)
    out: Forecast = {}
    for item in raw:
        h = int(item["horizon"])
        if item["yhat"] is None:
            raise RuntimeError(f"project adapter returned NULL at origin {e} h={h}")
        out[h] = Prediction(
            float(item["yhat"]),
            float(item["lower_bound"]),
            float(item["upper_bound"]),
        )
    return out


def make_project_fn(
    values: tuple[float, ...], params: dict[str, float | int], seed: int
) -> PredictFn:
    def predict(e: int) -> Forecast:
        return forecast_project(values, e, params, seed)

    return predict


def make_ensemble_fn(
    component_fns: dict[str, PredictFn], weights: tuple[float, float, float]
) -> PredictFn:
    """Convex combination of the three components; PIs combine with the same
    weights (lower = sum w_i * lower_i, upper = sum w_i * upper_i)."""

    def predict(e: int) -> Forecast:
        parts = [component_fns[name](e) for name in ENSEMBLE_COMPONENTS]
        out: Forecast = {}
        for h in HORIZONS:
            yhat = sum(w * fc[h].yhat for w, fc in zip(weights, parts, strict=True))
            lo = sum(w * fc[h].lower for w, fc in zip(weights, parts, strict=True))
            hi = sum(w * fc[h].upper for w, fc in zip(weights, parts, strict=True))
            fb = any(fc[h].fallback for fc in parts)
            out[h] = Prediction(_clip0(yhat), _clip0(lo), max(_clip0(hi), _clip0(lo)), fb)
        return out

    return predict


# --- model registry -------------------------------------------------------------
@dataclass
class Registry:
    """All model predict functions plus shared fallback bookkeeping."""

    fns: dict[str, PredictFn]
    arima_stats: ArimaStats = field(default_factory=ArimaStats)
    subepidemic_stats: SubepidemicStats = field(default_factory=SubepidemicStats)
    ensemble_stats: EnsembleSubepidemicStats = field(default_factory=EnsembleSubepidemicStats)
    prophet_stats: ProphetStats = field(default_factory=ProphetStats)

    @property
    def active_models(self) -> list[str]:
        """MODEL_IDS restricted to the functions actually registered."""
        return [m for m in MODEL_IDS if m in self.fns]


def build_registry(
    values: tuple[float, ...],
    qwen_params: dict[str, float | int],
    ensemble_weights: tuple[float, float, float],
    seed: int = SEED,
) -> Registry:
    """Build all model functions over a fixed series (sentinel-friendly)."""
    registry = Registry(fns={})
    registry.fns["paper_arima"] = make_arima_fn(values, registry.arima_stats)
    registry.fns["paper_gam"] = make_gam_fn(values)
    registry.fns["paper_slr"] = make_slr_fn(values)
    registry.fns["paper_subepidemic_top"] = make_subepidemic_fn(values, registry.subepidemic_stats)
    registry.fns["project_kg_primary"] = make_project_fn(values, KG_DEFAULT_PARAMS, seed)
    registry.fns["project_qwen_tuned"] = make_project_fn(values, qwen_params, seed)
    for model in BASELINE_IDS:
        registry.fns[model] = make_baseline_fn(values, model)
    registry.fns["project_kg_ensemble"] = make_ensemble_fn(
        {name: registry.fns[name] for name in ENSEMBLE_COMPONENTS}, ensemble_weights
    )
    # Gap-closure round: multi-wave sub-epidemic ensembles share one memoized
    # candidate cache per registry; paper_prophet is registered only when the
    # optional prophet dependency imports.
    get_candidates = make_candidate_cache(values)
    registry.fns["paper_subepidemic_second"] = make_subepidemic_multi_fn(
        "second", get_candidates, registry.ensemble_stats
    )
    registry.fns["paper_subepidemic_ensemble_weighted"] = make_subepidemic_multi_fn(
        "weighted", get_candidates, registry.ensemble_stats
    )
    registry.fns["paper_subepidemic_ensemble_unweighted"] = make_subepidemic_multi_fn(
        "unweighted", get_candidates, registry.ensemble_stats
    )
    if prophet_available():
        registry.fns["paper_prophet"] = make_prophet_fn(values, registry.prophet_stats)
    return registry


# --- metrics --------------------------------------------------------------------
Pair = tuple[float, float, float, float]  # (actual, yhat, lower, upper)


def mase_denominator(values: tuple[float, ...], origins: Sequence[int]) -> float:
    """Mean over origins of Q_e = mean(|values[i] - values[i-52]|), i in [52, e-1]."""
    per_origin = []
    for e in origins:
        diffs = [abs(values[i] - values[i - 52]) for i in range(52, e)]
        per_origin.append(sum(diffs) / len(diffs))
    return sum(per_origin) / len(per_origin)


def metric_bundle(pairs: Sequence[Pair], mase_denom: float) -> dict[str, float]:
    """All SPEC metrics over a set of (actual, yhat, lower, upper) pairs."""
    n = len(pairs)
    if n == 0:
        raise ValueError("metric_bundle needs at least one pair")
    abs_err = [abs(a - p) for a, p, _lo, _hi in pairs]
    sq_err = [(a - p) ** 2 for a, p, _lo, _hi in pairs]
    mae = sum(abs_err) / n
    mse = sum(sq_err) / n
    wape = sum(abs_err) / sum(abs(a) for a, _p, _lo, _hi in pairs)
    smape = (
        sum(
            0.0 if abs(a) + abs(p) == 0.0 else 2.0 * abs(a - p) / (abs(a) + abs(p))
            for a, p, _lo, _hi in pairs
        )
        / n
    )
    coverage = sum(1 for a, _p, lo, hi in pairs if lo <= a <= hi) / n
    width = sum(hi - lo for _a, _p, lo, hi in pairs) / n
    return {
        "n": float(n),
        "MAE": mae,
        "MSE": mse,
        "RMSE": math.sqrt(mse),
        "WAPE": wape,
        # agreement_pct = max(0, 1 - WAPE) * 100; NOT a classification accuracy.
        "agreement_pct": max(0.0, 1.0 - wape) * 100.0,
        "sMAPE": smape,
        "MASE": mae / mase_denom,
        "coverage_95": coverage,
        "mean_interval_width": width,
    }


# --- exactly-once evaluation protocol -------------------------------------------
@dataclass
class Protocol:
    """Ledger enforcing: validation selection first, test evaluated once."""

    validation_selection_completed: bool = False
    test_evaluated_count: int = 0

    def complete_validation_selection(self) -> None:
        if self.validation_selection_completed:
            raise RuntimeError("validation selection already completed")
        self.validation_selection_completed = True

    def record_test_evaluation(self) -> None:
        if not self.validation_selection_completed:
            raise RuntimeError("test evaluated before validation selection froze")
        self.test_evaluated_count += 1

    def ledger(self) -> dict[str, object]:
        return {
            "validation_selection_completed": self.validation_selection_completed,
            "test_evaluated_count": self.test_evaluated_count,
        }


# --- pipeline ---------------------------------------------------------------------
@dataclass(frozen=True)
class ForecastRecord:
    split: str
    origin_index: int
    horizon: int
    target_index: int
    actual: float
    model: str
    yhat: float
    lower: float
    upper: float
    fallback: bool


@dataclass
class SelectionResult:
    qwen_params: dict[str, float | int]
    qwen_grid_rows: list[dict[str, float | int]]
    ensemble_weights: tuple[float, float, float]
    ensemble_grid_rows: list[dict[str, float | int]]


@dataclass
class PipelineResult:
    records: list[ForecastRecord]
    selection: SelectionResult
    arima_stats: ArimaStats
    subepidemic_stats: SubepidemicStats
    ensemble_stats: EnsembleSubepidemicStats
    prophet_stats: ProphetStats
    active_models: list[str]
    ledger: dict[str, object]


def _valid_pairs(values: tuple[float, ...], e: int) -> list[tuple[int, int]]:
    """(horizon, target_index) pairs valid at origin e (t_idx = e + h - 1 <= 411)."""
    return [(h, e + h - 1) for h in HORIZONS if e + h - 1 < len(values)]


def _score_pairs(actual: list[float], pred: list[float]) -> dict[str, float]:
    pairs: list[Pair] = [(a, p, 0.0, 0.0) for a, p in zip(actual, pred, strict=True)]
    bundle = metric_bundle(pairs, mase_denom=1.0)
    return {"wape": bundle["WAPE"], "rmse": bundle["RMSE"], "mae": bundle["MAE"]}


def _select_qwen_params(
    values: tuple[float, ...], seed: int
) -> tuple[dict[str, float | int], list[dict[str, float | int]]]:
    """Qwen controlled tuning: whitelist grid scored by validation WAPE only.

    Tie-breaks: WAPE -> RMSE -> parameter lexicographic order.  Test labels are
    not touched here.
    """
    grid_rows: list[dict[str, float | int]] = []
    for params in QWEN_GRID:
        actual: list[float] = []
        pred: list[float] = []
        for e in VAL_ORIGINS:
            forecast = forecast_project(values, e, params, seed)
            for h, t_idx in _valid_pairs(values, e):
                actual.append(values[t_idx])
                pred.append(forecast[h].yhat)
        scores = _score_pairs(actual, pred)
        grid_rows.append({**params, **scores})
    grid_rows.sort(
        key=lambda r: (
            float(r["wape"]),
            float(r["rmse"]),
            int(r["n_estimators"]),
            float(r["learning_rate"]),
            int(r["min_samples_leaf"]),
        )
    )
    best = {key: grid_rows[0][key] for key in ("n_estimators", "learning_rate", "min_samples_leaf")}
    for row in grid_rows:
        row["selected"] = int(
            row["n_estimators"] == best["n_estimators"]
            and row["learning_rate"] == best["learning_rate"]
            and row["min_samples_leaf"] == best["min_samples_leaf"]
        )
    return best, grid_rows


def _select_ensemble_weights(
    values: tuple[float, ...],
    qwen_params: dict[str, float | int],
    seed: int,
) -> tuple[tuple[float, float, float], list[dict[str, float | int]]]:
    """Convex-combination weights on a 0.05 simplex grid, validation WAPE only.

    Tie-breaks: WAPE -> RMSE -> higher project_qwen_tuned weight, then higher
    seasonal_naive weight for full determinism.
    """
    component_pred: dict[str, list[float]] = {name: [] for name in ENSEMBLE_COMPONENTS}
    actual: list[float] = []
    for e in VAL_ORIGINS:
        qwen_fc = forecast_project(values, e, qwen_params, seed)
        seasonal_fc = make_baseline_fn(values, "seasonal_naive")(e)
        last_fc = make_baseline_fn(values, "last_value")(e)
        for h, t_idx in _valid_pairs(values, e):
            actual.append(values[t_idx])
            component_pred["project_qwen_tuned"].append(qwen_fc[h].yhat)
            component_pred["seasonal_naive"].append(seasonal_fc[h].yhat)
            component_pred["last_value"].append(last_fc[h].yhat)
    grid_rows = []
    step = ENSEMBLE_GRID_STEP
    for w_q in range(step + 1):
        for w_s in range(step + 1 - w_q):
            w_l = step - w_q - w_s
            weights = (w_q / step, w_s / step, w_l / step)
            blend = [
                sum(
                    w * component_pred[name][i]
                    for w, name in zip(weights, ENSEMBLE_COMPONENTS, strict=True)
                )
                for i in range(len(actual))
            ]
            scores = _score_pairs(actual, blend)
            grid_rows.append(
                {
                    "w_project_qwen_tuned": weights[0],
                    "w_seasonal_naive": weights[1],
                    "w_last_value": weights[2],
                    **scores,
                }
            )
    grid_rows.sort(
        key=lambda r: (
            float(r["wape"]),
            float(r["rmse"]),
            -float(r["w_project_qwen_tuned"]),
            -float(r["w_seasonal_naive"]),
        )
    )
    top = grid_rows[0]
    weights = (
        float(top["w_project_qwen_tuned"]),
        float(top["w_seasonal_naive"]),
        float(top["w_last_value"]),
    )
    for row in grid_rows:
        row["selected"] = int(
            row["w_project_qwen_tuned"] == weights[0]
            and row["w_seasonal_naive"] == weights[1]
            and row["w_last_value"] == weights[2]
        )
    return weights, grid_rows


def _evaluate_origins(
    registry: Registry,
    values: tuple[float, ...],
    origins: Sequence[int],
    split: str,
) -> list[ForecastRecord]:
    records: list[ForecastRecord] = []
    for e in origins:
        for model in registry.active_models:
            forecast = registry.fns[model](e)
            for h, t_idx in _valid_pairs(values, e):
                p = forecast[h]
                records.append(
                    ForecastRecord(
                        split=split,
                        origin_index=e,
                        horizon=h,
                        target_index=t_idx,
                        actual=values[t_idx],
                        model=model,
                        yhat=p.yhat,
                        lower=p.lower,
                        upper=p.upper,
                        fallback=p.fallback,
                    )
                )
    return records


def run_pipeline(series: Series, seed: int = SEED) -> PipelineResult:
    """Full deterministic run: validation selection, then a single test pass."""
    values = series.values
    protocol = Protocol()

    # --- validation phase: selection happens here and only here. ---
    qwen_params, qwen_grid_rows = _select_qwen_params(values, seed)
    ensemble_weights, ensemble_grid_rows = _select_ensemble_weights(values, qwen_params, seed)
    protocol.complete_validation_selection()
    selection = SelectionResult(
        qwen_params=qwen_params,
        qwen_grid_rows=qwen_grid_rows,
        ensemble_weights=ensemble_weights,
        ensemble_grid_rows=ensemble_grid_rows,
    )

    registry = build_registry(values, qwen_params, ensemble_weights, seed)
    records = _evaluate_origins(registry, values, VAL_ORIGINS, "validation")

    # --- test phase: evaluated exactly once, after every choice is frozen. ---
    test_records = _evaluate_origins(registry, values, TEST_ORIGINS, "test")
    protocol.record_test_evaluation()
    records.extend(test_records)

    return PipelineResult(
        records=records,
        selection=selection,
        arima_stats=registry.arima_stats,
        subepidemic_stats=registry.subepidemic_stats,
        ensemble_stats=registry.ensemble_stats,
        prophet_stats=registry.prophet_stats,
        active_models=registry.active_models,
        ledger=protocol.ledger(),
    )


# --- sentinel ---------------------------------------------------------------------
def sentinel_mutate(values: tuple[float, ...], e: int) -> tuple[float, ...]:
    """Return a copy of the series with the future values[e:] poisoned."""
    return values[:e] + (999999.0,) * (len(values) - e)


# --- equivalence checks (auto.arima / mgcv equivalents, non-replacing) ---------
def select_arima_grid_order(window: Sequence[float]) -> tuple[str, tuple[int, int, int] | None]:
    """Read-only mirror of forecast_arima_window's selection stage.

    Returns (stage, order): stage is "grid" (AICc grid winner), "chain"
    (fallback chain), or "mean" (last resort; order None).  Used only by the
    auto.arima equivalence check; paper_arima itself is untouched.
    """
    best: tuple[float, tuple[int, int, int]] | None = None
    for order in ARIMA_GRID:
        try:
            _result, aicc = _fit_arima(window, order)
        except Exception:
            continue
        if best is None or aicc < best[0]:
            best = (aicc, order)
    if best is not None:
        return "grid", best[1]
    for order in ARIMA_FALLBACK_CHAIN:
        try:
            _fit_arima(window, order)
        except Exception:
            continue
        return "chain", order
    return "mean", None


def autoarima_equivalence(series: Series) -> dict[str, Any]:
    """Compare pmdarima.auto_arima (stepwise, AICc) order selection against the
    paper_arima grid+AICc selection on all 100 validation+test 11-week windows.

    The search space is constrained to the same box as the grid
    (max_p=2, max_d=1, max_q=2, seasonal=False) so the comparison isolates the
    stepwise-vs-exhaustive search difference.  Deterministic: stepwise search
    with fixed random_state.  Read-only: nothing here feeds any model.
    """
    try:
        import pmdarima  # noqa: F401
    except Exception as exc:
        return {
            "status": "failed",
            "stage": "import",
            "error": f"{type(exc).__name__}: {exc}",
        }
    from pmdarima import auto_arima

    rows: list[dict[str, Any]] = []
    for split, origins in (("validation", VAL_ORIGINS), ("test", TEST_ORIGINS)):
        for e in origins:
            window = _window(series.values, e)
            stage, grid_order = select_arima_grid_order(window)
            auto_order: list[int] | None = None
            error: str | None = None
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    fit = auto_arima(
                        np.asarray(window, dtype=float),
                        seasonal=False,
                        stepwise=True,
                        max_p=2,
                        max_d=1,
                        max_q=2,
                        information_criterion="aicc",
                        suppress_warnings=True,
                        error_action="ignore",
                        random_state=SEED,
                        trace=False,
                    )
                if fit is not None:
                    auto_order = [int(v) for v in fit.order]
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
            rows.append(
                {
                    "split": split,
                    "origin_index": e,
                    "grid_stage": stage,
                    "grid_order": list(grid_order) if grid_order is not None else None,
                    "auto_arima_order": auto_order,
                    "auto_arima_error": error,
                    "orders_match": (
                        auto_order is not None
                        and grid_order is not None
                        and list(grid_order) == auto_order
                    ),
                }
            )
    compared = [r for r in rows if r["auto_arima_order"] is not None]
    agreed = sum(1 for r in compared if r["orders_match"])
    return {
        "status": "ok",
        "description": (
            "pmdarima.auto_arima(stepwise=True, information_criterion='aicc', "
            "seasonal=False, max_p=2, max_d=1, max_q=2, random_state=SEED) order "
            "selection vs the paper_arima exhaustive grid + statsmodels AICc "
            "selection, per 11-week calibration window.  Note: auto_arima's AICc "
            "is computed by its own state-space backend, so identical criteria "
            "definitions are not guaranteed; agreement is about selected orders."
        ),
        "pmdarima_version": str(pmdarima.__version__),
        "n_origins": len(rows),
        "n_compared": len(compared),
        "n_auto_arima_failures": len(rows) - len(compared),
        "n_orders_match": agreed,
        "agreement_rate": (agreed / len(compared)) if compared else None,
        "per_origin": rows,
    }


def gam_equivalence(series: Series) -> dict[str, Any]:
    """Compare pygam penalized-spline predictions against paper_gam (OLS
    B-spline + GCV) on the validation windows.

    2026-08-19 attempt record: pygam 0.12.0 requires scipy<1.17,>=1.11.1, so
    `pip install pygam` downgraded scipy 1.18.0 -> 1.16.3.  That changed
    scipy.optimize.least_squares numerics and drifted 748
    paper_subepidemic_top prediction rows, violating the hard byte-identity
    invariant for the original ten models.  pygam was uninstalled and scipy
    restored to 1.18.0 (spot check: old rows byte-identical again).  The
    comparison therefore does not run; the failure is recorded here and in the
    manifest under new_gap_attempts.
    """
    try:
        import pygam  # noqa: F401
    except Exception as exc:
        return {
            "status": "not_run",
            "stage": "import",
            "import_error": f"{type(exc).__name__}: {exc}",
            "reason": (
                "pygam 0.12.0 pins scipy<1.17,>=1.11.1; installing it downgraded "
                "scipy 1.18.0 -> 1.16.3, which perturbed scipy least_squares and "
                "changed 748 paper_subepidemic_top prediction rows (byte-level "
                "drift), violating the hard byte-identity invariant of the "
                "original ten models.  pygam (and its extra deps progressbar2, "
                "python-utils) was uninstalled and scipy restored to 1.18.0; "
                "pip check is clean afterwards."
            ),
            "commands": [
                "pip install pygam  # -> scipy downgraded 1.18.0 -> 1.16.3",
                "pip uninstall -y pygam progressbar2 python-utils",
                "pip install scipy==1.18.0",
                "pip check  # clean",
            ],
        }
    from pygam import LinearGAM, s

    rows: list[dict[str, Any]] = []
    for e in VAL_ORIGINS:
        window = _window(series.values, e)
        ours = forecast_gam_window(window)
        t = np.arange(1, PAPER_WINDOW + 1, dtype=float)
        y = np.asarray(window, dtype=float)
        t_pred = np.arange(PAPER_WINDOW + 1, PAPER_WINDOW + 5, dtype=float)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            gam = LinearGAM(s(0, spline_order=3, n_splines=5)).fit(t, y)
            pred = np.asarray(gam.predict(t_pred), dtype=float)
        ours_pts = np.asarray([ours[h].yhat for h in HORIZONS], dtype=float)
        diff = np.abs(pred - ours_pts)
        rows.append(
            {
                "origin_index": e,
                "paper_gam": [float(v) for v in ours_pts],
                "pygam": [float(v) for v in pred],
                "max_abs_diff": float(diff.max()),
                "mean_abs_diff": float(diff.mean()),
            }
        )
    all_ours = np.asarray([v for r in rows for v in r["paper_gam"]], dtype=float)
    all_pygam = np.asarray([v for r in rows for v in r["pygam"]], dtype=float)
    corr = float(np.corrcoef(all_ours, all_pygam)[0, 1]) if len(all_ours) > 1 else None
    return {
        "status": "ok",
        "description": (
            "pygam LinearGAM(s(0, spline_order=3, n_splines=5), default lambda) "
            "vs paper_gam (patsy cubic B-spline + OLS + GCV df selection) point "
            "forecasts at t=12..15, per validation origin.  Both approximate "
            "mgcv; differences reflect basis/smoothing choices, not leakage."
        ),
        "n_origins": len(rows),
        "max_abs_diff_overall": float(max(r["max_abs_diff"] for r in rows)),
        "mean_abs_diff_overall": float(np.mean([r["mean_abs_diff"] for r in rows])),
        "prediction_correlation": corr,
        "per_origin": rows,
    }


def forecasts_equal(a: Forecast, b: Forecast) -> bool:
    """Bitwise equality of every (yhat, lower, upper, fallback) across horizons."""
    if set(a) != set(b):
        return False
    return all(
        (a[h].yhat, a[h].lower, a[h].upper, a[h].fallback)
        == (b[h].yhat, b[h].lower, b[h].upper, b[h].fallback)
        for h in a
    )
