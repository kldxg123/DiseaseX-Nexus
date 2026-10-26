"""Extended whitelist candidate models for the KG_QWEN autotuning loop.

Four additive fit/predict adapters registered into
``kg_r3c.execution.MODEL_FITS`` / ``MODEL_PREDICTS`` via
:func:`register_extended_models` (idempotent, never touches baselines):

    seasonal_negbin_glm.v1    seasonal-dummy IRLS negative-binomial GLM
    fourier_negbin_glm.v1     Fourier-pair IRLS negative-binomial GLM
    lagged_count_boosting.v1  gradient-boosted stumps on lag/rolling features
    seasonal_ensemble.v1      weight * seasonal_naive + (1-weight) * component

Safety invariants enforced here (same discipline as ``kg_r3c.execution``):
  * fit only ever uses rows with ``period_start <= cutoff``;
  * missing observations stay None: training skips them (never filled,
    zeroed or interpolated) and predictions whose required lag/rolling
    features are missing return NULL with ``NONE_MISSING_LAG``;
  * predictions are non-negative and finite; intervals are plug-in NB
    quantiles via ``execution.nb_quantiles`` (GLMs), empirical in-sample
    1-step residual quantiles scaled by sqrt(h) (boosting), or weighted
    component intervals (ensemble);
  * pure stdlib only (math/dataclasses/datetime): linear systems are solved
    by Gaussian elimination with partial pivoting plus ridge diagonal
    loading, so no numpy/scipy/pandas/sklearn import exists;
  * no wall-clock or global randomness enters any artifact (boosting is
    fully deterministic and uses no randomness at all), so identical input
    + seed reproduces byte-identical ``artifact_sha256`` values.
"""

from __future__ import annotations

import math
from dataclasses import asdict
from datetime import date, timedelta
from typing import Any

from epidemic_core.kg_r3c import execution
from epidemic_core.kg_r3c.execution import (
    INTERVAL_LEVEL,
    FittedModelArtifact,
    TrainingRow,
    make_artifact,
    nb_quantiles,
)

SEASONAL_NEGBIN_GLM_ID = "seasonal_negbin_glm.v1"
FOURIER_NEGBIN_GLM_ID = "fourier_negbin_glm.v1"
LAGGED_COUNT_BOOSTING_ID = "lagged_count_boosting.v1"
SEASONAL_ENSEMBLE_ID = "seasonal_ensemble.v1"
EXTENDED_ADAPTER_IDS: tuple[str, ...] = (
    SEASONAL_NEGBIN_GLM_ID,
    FOURIER_NEGBIN_GLM_ID,
    LAGGED_COUNT_BOOSTING_ID,
    SEASONAL_ENSEMBLE_ID,
)

ENSEMBLE_COMPONENT_CANDIDATES: tuple[str, ...] = (
    "negbin_dglm.v1",
    SEASONAL_NEGBIN_GLM_ID,
    FOURIER_NEGBIN_GLM_ID,
)

NULL_MISSING_LAG = "NONE_MISSING_LAG"
NULL_ALL_COMPONENTS = "NONE_ALL_COMPONENTS_NULL"
ENSEMBLE_INTERVAL_METHOD = "WEIGHTED_ENSEMBLE"
BOOSTING_INTERVAL_METHOD = "EMPIRICAL_RESIDUAL"
GLM_INTERVAL_METHOD = "NB_PLUG_IN_PMF"

_IRLS_MAX_ITER = 100
_IRLS_TOL = 1e-10
_ETA_CLAMP = 30.0
_PIVOT_EPS = 1e-12


# ------------------------------------------------------------------- helpers
def _clip(value: float, lower: float, upper: float) -> float:
    return min(max(value, lower), upper)


def _clip_int(value: float, lower: int, upper: int) -> int:
    return int(_clip(float(round(value)), float(lower), float(upper)))


def _infer_frequency(rows: list[TrainingRow], parameters: dict[str, Any]) -> str:
    """Frequency-aware seasonality: 'week' -> period 52, 'month' -> 12.

    The execution ``MODEL_FITS`` signature carries no frequency argument, so
    it is inferred from the median spacing of ``period_start`` (weekly grids
    space ~7 days, monthly grids ~28-31 days). An explicit
    ``parameters['frequency']`` override wins when present.
    """
    explicit = parameters.get("frequency")
    if explicit in ("week", "month"):
        return str(explicit)
    deltas = [
        (b.period_start - a.period_start).days for a, b in zip(rows, rows[1:], strict=False)
    ]
    if deltas:
        ordered = sorted(deltas)
        median = ordered[len(ordered) // 2]
        if median <= 8:
            return "week"
    return "month"


def _seasonal_period(frequency: str) -> int:
    return 52 if frequency == "week" else 12


def _period_add(period: date, frequency: str, n: int) -> date:
    """Same semantics as forecast_model_kg.dual_loop.period_add (kept local so
    this module depends only on kg_r3c.execution)."""
    if frequency == "week":
        return period + timedelta(weeks=n)
    month = period.month - 1 + n
    return date(period.year + month // 12, month % 12 + 1, 1)


def _season_index(target: date, position: int, frequency: str) -> int:
    """Seasonal category in [0, period): month-of-year for monthly series,
    series position modulo 52 for weekly series (mirrors the seasonal-naive
    lag-52 convention)."""
    if frequency == "week":
        return position % 52
    return target.month - 1


def _solve_linear(matrix: list[list[float]], rhs: list[float]) -> list[float]:
    """Gaussian elimination with partial pivoting. The caller ridge-loads the
    diagonal; a degenerate pivot is additionally bumped to stay solvable.
    Fully deterministic (first max pivot wins ties)."""
    n = len(matrix)
    aug = [list(matrix[i]) + [rhs[i]] for i in range(n)]
    for col in range(n):
        piv = col
        for r in range(col + 1, n):
            if abs(aug[r][col]) > abs(aug[piv][col]):
                piv = r
        if abs(aug[piv][col]) < _PIVOT_EPS:
            aug[piv][col] = _PIVOT_EPS
        aug[col], aug[piv] = aug[piv], aug[col]
        pivot_val = aug[col][col]
        for r in range(col + 1, n):
            factor = aug[r][col] / pivot_val
            if factor == 0.0:
                continue
            for c in range(col, n + 1):
                aug[r][c] -= factor * aug[col][c]
    out = [0.0] * n
    for r in range(n - 1, -1, -1):
        residual = aug[r][n]
        for c in range(r + 1, n):
            residual -= aug[r][c] * out[c]
        out[r] = residual / aug[r][r]
    return out


def _irls_fit(x_rows: list[list[float]], y: list[float], shape: float, ridge: float) -> list[float]:
    """IRLS for a log-link NB GLM with fixed shape (mean fit only; the
    mean-variance pair is never estimated jointly). Working weights
    w = mu / (1 + mu/shape), ridge-loaded normal equations."""
    n_par = len(x_rows[0])
    beta = [0.0] * n_par
    beta[0] = math.log(max(sum(y) / len(y), 1e-6))
    for _ in range(_IRLS_MAX_ITER):
        xtw_x = [[0.0] * n_par for _ in range(n_par)]
        xtw_z = [0.0] * n_par
        for xi, yi in zip(x_rows, y, strict=False):
            eta = _clip(sum(b * x for b, x in zip(beta, xi, strict=False)), -_ETA_CLAMP, _ETA_CLAMP)
            mu = math.exp(eta)
            w = mu / (1.0 + mu / shape)
            z = eta + (yi - mu) / max(mu, 1e-9)
            for a in range(n_par):
                wa = w * xi[a]
                xtw_z[a] += wa * z
                row_a = xtw_x[a]
                for b in range(a, n_par):
                    row_a[b] += wa * xi[b]
        for a in range(n_par):
            xtw_x[a][a] += ridge
            for b in range(a):
                xtw_x[a][b] = xtw_x[b][a]
        new_beta = _solve_linear(xtw_x, xtw_z)
        delta = max(abs(nb - ob) for nb, ob in zip(new_beta, beta, strict=False))
        beta = new_beta
        if delta < _IRLS_TOL:
            break
    return beta


def _glm_predict_out(
    beta: list[float], x: list[float], shape: float, horizon: int
) -> dict[str, Any]:
    eta = _clip(sum(b * v for b, v in zip(beta, x, strict=False)), -_ETA_CLAMP, _ETA_CLAMP)
    mu = max(math.exp(eta), 0.0)
    lo, hi = nb_quantiles(mu, shape)
    return {
        "horizon": horizon,
        "yhat": round(mu, 6),
        "lower_bound": lo,
        "upper_bound": hi,
        "interval_level": INTERVAL_LEVEL,
        "interval_method": GLM_INTERVAL_METHOD,
    }


def _null_prediction(horizon: int, method: str = NULL_MISSING_LAG) -> dict[str, Any]:
    return {
        "horizon": horizon,
        "yhat": None,
        "lower_bound": None,
        "upper_bound": None,
        "interval_level": INTERVAL_LEVEL,
        "interval_method": method,
    }


# ------------------------------------------------------- seasonal_negbin_glm
def _seasonal_design(season: int, period: int, trend_t: float, trend: int) -> list[float]:
    x = [1.0]
    for k in range(1, period):  # baseline category 0 omitted
        x.append(1.0 if season == k else 0.0)
    if trend:
        x.append(trend_t)
    return x


def fit_seasonal_negbin_glm(
    rows: list[TrainingRow], parameters: dict[str, Any], seed: int, cutoff: date
) -> FittedModelArtifact:
    """Log-link NB GLM: intercept + seasonal categorical effect (month -> 11
    dummies, week -> 51 dummies, baseline category omitted) + optional linear
    trend on normalized time. IRLS mean fit, NB shape fixed by parameter.

    Parameters (clipped to contract ranges):
      shape in [0.5, 500], trend in {0, 1}, ridge in [1e-6, 10].
    """
    shape = _clip(float(parameters.get("shape", 10.0)), 0.5, 500.0)
    trend = _clip_int(float(parameters.get("trend", 0)), 0, 1)
    ridge = _clip(float(parameters.get("ridge", 1e-3)), 1e-6, 10.0)
    frequency = _infer_frequency(rows, parameters)
    period = _seasonal_period(frequency)
    usable = [r for r in rows if r.period_start <= cutoff]
    n = len(usable)
    x_rows: list[list[float]] = []
    y: list[float] = []
    for i, row in enumerate(usable):
        if row.value is None:
            continue  # missing stays missing: skip, never fill
        season = _season_index(row.period_start, i, frequency)
        trend_t = i / max(n - 1, 1)
        x_rows.append(_seasonal_design(season, period, trend_t, trend))
        y.append(max(float(row.value), 0.0))
    width = period + trend
    beta = _irls_fit(x_rows, y, shape, ridge) if y else [0.0] * width
    state = {
        "model": SEASONAL_NEGBIN_GLM_ID,
        "frequency": frequency,
        "period": period,
        "coefficients": beta,
        "shape": shape,
        "trend": trend,
        "ridge": ridge,
        "n_train": n,
        "n_observed": len(y),
    }
    return make_artifact(SEASONAL_NEGBIN_GLM_ID, seed, state, usable)


def predict_seasonal_negbin_glm(
    fitted: FittedModelArtifact, horizons: list[int], issue_period: date
) -> list[dict[str, Any]]:
    st = fitted.state
    period = int(st["period"])
    frequency = str(st["frequency"])
    n = int(st["n_train"])
    beta = [float(b) for b in st["coefficients"]]
    shape = float(st["shape"])
    trend = int(st["trend"])
    out = []
    for h in horizons:
        target = _period_add(issue_period, frequency, h)
        season = _season_index(target, n - 1 + h, frequency)
        trend_t = (n - 1 + h) / max(n - 1, 1)
        out.append(_glm_predict_out(beta, _seasonal_design(season, period, trend_t, trend), shape, h))
    return out


# -------------------------------------------------------- fourier_negbin_glm
def _fourier_design(position: int, period: int, k_pairs: int, trend_t: float, trend: int) -> list[float]:
    x = [1.0]
    for k in range(1, k_pairs + 1):
        angle = 2.0 * math.pi * k * position / period
        x.append(math.sin(angle))
        x.append(math.cos(angle))
    if trend:
        x.append(trend_t)
    return x


def fit_fourier_negbin_glm(
    rows: list[TrainingRow], parameters: dict[str, Any], seed: int, cutoff: date
) -> FittedModelArtifact:
    """Log-link NB GLM: intercept + K Fourier sin/cos pairs (period 12 for
    monthly, 52 for weekly profiles) + optional trend + ridge. IRLS mean fit
    with fixed NB shape.

    Parameters (clipped to contract ranges):
      K in {1, 2, 3}, trend in {0, 1}, ridge in [1e-6, 10], shape in [0.5, 500].
    """
    k_pairs = _clip_int(float(parameters.get("K", 2)), 1, 3)
    trend = _clip_int(float(parameters.get("trend", 0)), 0, 1)
    ridge = _clip(float(parameters.get("ridge", 1e-3)), 1e-6, 10.0)
    shape = _clip(float(parameters.get("shape", 10.0)), 0.5, 500.0)
    frequency = _infer_frequency(rows, parameters)
    period = _seasonal_period(frequency)
    usable = [r for r in rows if r.period_start <= cutoff]
    n = len(usable)
    x_rows: list[list[float]] = []
    y: list[float] = []
    for i, row in enumerate(usable):
        if row.value is None:
            continue
        trend_t = i / max(n - 1, 1)
        x_rows.append(_fourier_design(i, period, k_pairs, trend_t, trend))
        y.append(max(float(row.value), 0.0))
    width = 1 + 2 * k_pairs + trend
    beta = _irls_fit(x_rows, y, shape, ridge) if y else [0.0] * width
    state = {
        "model": FOURIER_NEGBIN_GLM_ID,
        "frequency": frequency,
        "period": period,
        "K": k_pairs,
        "coefficients": beta,
        "shape": shape,
        "trend": trend,
        "ridge": ridge,
        "n_train": n,
        "n_observed": len(y),
    }
    return make_artifact(FOURIER_NEGBIN_GLM_ID, seed, state, usable)


def predict_fourier_negbin_glm(
    fitted: FittedModelArtifact, horizons: list[int], issue_period: date
) -> list[dict[str, Any]]:
    st = fitted.state
    period = int(st["period"])
    k_pairs = int(st["K"])
    n = int(st["n_train"])
    beta = [float(b) for b in st["coefficients"]]
    shape = float(st["shape"])
    trend = int(st["trend"])
    out = []
    for h in horizons:
        trend_t = (n - 1 + h) / max(n - 1, 1)
        out.append(_glm_predict_out(beta, _fourier_design(n - 1 + h, period, k_pairs, trend_t, trend), shape, h))
    return out


# ------------------------------------------------------ lagged_count_boosting
LAG_LAGS: tuple[int, ...] = (1, 2, 3, 6, 12)
ROLL_WINDOWS: tuple[int, ...] = (3, 6, 12)
FEATURE_NAMES: tuple[str, ...] = tuple(f"lag_{k}" for k in LAG_LAGS) + tuple(
    f"rolling_mean_{k}" for k in ROLL_WINDOWS
)
MAX_HISTORY = 12


def _boost_features(history: list[float | None]) -> list[float | None]:
    """Lag 1/2/3/6/12 + rolling means 3/6/12 over the history tail only
    (origin-and-past data plus recursively backfilled predictions). Any None
    inside a required window makes that feature None."""
    n = len(history)
    feats: list[float | None] = []
    for k in LAG_LAGS:
        feats.append(history[n - k] if n >= k else None)
    for k in ROLL_WINDOWS:
        if n < k:
            feats.append(None)
            continue
        window = history[n - k :]
        if any(v is None for v in window):
            feats.append(None)
        else:
            feats.append(sum(v for v in window if v is not None) / k)
    return feats


def _best_stump(
    samples_x: list[list[float]], residuals: list[float], min_samples_leaf: int
) -> tuple[int, float, float, float] | None:
    """Best (feature, threshold, left_mean, right_mean) least-squares stump.
    Deterministic: strictly-smaller SSE wins, first found wins ties."""
    n = len(residuals)
    n_features = len(samples_x[0])
    sum_total = sum(residuals)
    sq_total = sum(r * r for r in residuals)
    best_sse: float | None = None
    best: tuple[int, float, float, float] | None = None
    for j in range(n_features):
        order = sorted(range(n), key=lambda i: samples_x[i][j])
        sum_l = 0.0
        sq_l = 0.0
        for pos in range(n - 1):
            r = residuals[order[pos]]
            sum_l += r
            sq_l += r * r
            if samples_x[order[pos]][j] == samples_x[order[pos + 1]][j]:
                continue
            nl = pos + 1
            nr = n - nl
            if nl < min_samples_leaf or nr < min_samples_leaf:
                continue
            sum_r = sum_total - sum_l
            sq_r = sq_total - sq_l
            sse = (sq_l - sum_l * sum_l / nl) + (sq_r - sum_r * sum_r / nr)
            if best_sse is None or sse < best_sse:
                threshold = (samples_x[order[pos]][j] + samples_x[order[pos + 1]][j]) / 2.0
                best_sse = sse
                best = (j, threshold, sum_l / nl, sum_r / nr)
    return best


def _quantile(sorted_values: list[float], p: float) -> float:
    """Linear-interpolation empirical quantile of a sorted sample."""
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = p * (len(sorted_values) - 1)
    lower_i = int(math.floor(pos))
    frac = pos - lower_i
    if lower_i + 1 >= len(sorted_values):
        return sorted_values[-1]
    return sorted_values[lower_i] * (1.0 - frac) + sorted_values[lower_i + 1] * frac


def fit_lagged_count_boosting(
    rows: list[TrainingRow], parameters: dict[str, Any], seed: int, cutoff: date
) -> FittedModelArtifact:
    """Gradient-boosted stumps (depth 1) on lag/rolling features, boosting the
    MSE of log1p(y); outputs are expm1'd and truncated at >= 0. Fully
    deterministic: no randomness is consumed anywhere in fit or predict.

    Parameters (clipped to contract ranges):
      n_estimators in [10, 100], learning_rate in [0.01, 0.5],
      min_samples_leaf in [5, 30].
    """
    n_estimators = _clip_int(float(parameters.get("n_estimators", 50)), 10, 100)
    learning_rate = _clip(float(parameters.get("learning_rate", 0.1)), 0.01, 0.5)
    min_samples_leaf = _clip_int(float(parameters.get("min_samples_leaf", 10)), 5, 30)
    usable = [r for r in rows if r.period_start <= cutoff]
    values: list[float | None] = [
        max(float(r.value), 0.0) if r.value is not None else None for r in usable
    ]
    samples_x: list[list[float]] = []
    samples_logy: list[float] = []
    samples_raw: list[float] = []
    history: list[float | None] = []
    for v in values:
        feats = _boost_features(history)
        if v is not None and all(f is not None for f in feats):
            samples_x.append([float(f) for f in feats if f is not None])
            samples_logy.append(math.log1p(v))
            samples_raw.append(v)
        history.append(v)
    f0 = sum(samples_logy) / len(samples_logy) if samples_logy else 0.0
    current = [f0] * len(samples_logy)
    stumps: list[dict[str, float]] = []
    for _ in range(n_estimators):
        if len(samples_logy) < 2 * min_samples_leaf:
            break
        residuals = [t - f for t, f in zip(samples_logy, current, strict=False)]
        stump = _best_stump(samples_x, residuals, min_samples_leaf)
        if stump is None:
            break
        j, threshold, left, right = stump
        stumps.append(
            {"feature": float(j), "threshold": threshold, "left": left, "right": right}
        )
        for i, xi in enumerate(samples_x):
            current[i] += learning_rate * (left if xi[j] <= threshold else right)
    resid = sorted(
        raw - max(math.expm1(f), 0.0)
        for raw, f in zip(samples_raw, current, strict=False)
    )
    state = {
        "model": LAGGED_COUNT_BOOSTING_ID,
        "f0": f0,
        "learning_rate": learning_rate,
        "stumps": stumps,
        "n_estimators_requested": n_estimators,
        "min_samples_leaf": min_samples_leaf,
        "feature_names": list(FEATURE_NAMES),
        "resid_q025": _quantile(resid, 0.025),
        "resid_q975": _quantile(resid, 0.975),
        "n_samples": len(samples_logy),
        "tail": values[-MAX_HISTORY:],
    }
    return make_artifact(LAGGED_COUNT_BOOSTING_ID, seed, state, usable)


def predict_lagged_count_boosting(
    fitted: FittedModelArtifact, horizons: list[int], issue_period: date
) -> list[dict[str, Any]]:
    """Recursive multi-step: each prediction is backfilled into the feature
    history. A horizon whose lag/rolling features contain None returns a NULL
    prediction (NONE_MISSING_LAG); the gap then propagates to all later
    horizons by construction."""
    st = fitted.state
    history: list[float | None] = list(st["tail"])
    stumps = st["stumps"]
    f0 = float(st["f0"])
    learning_rate = float(st["learning_rate"])
    q_lo = float(st["resid_q025"])
    q_hi = float(st["resid_q975"])
    results: dict[int, dict[str, Any]] = {}
    max_h = max(horizons) if horizons else 0
    null_seen = False
    for h in range(1, max_h + 1):
        feats = _boost_features(history)
        if null_seen or any(f is None for f in feats):
            null_seen = True
            if h in horizons:
                results[h] = _null_prediction(h)
            continue
        xf = [float(f) for f in feats if f is not None]
        fval = f0
        for stump in stumps:
            j = int(stump["feature"])
            fval += learning_rate * (
                stump["left"] if xf[j] <= stump["threshold"] else stump["right"]
            )
        yhat = max(math.expm1(fval), 0.0)
        history.append(yhat)
        scale = math.sqrt(h)
        lo = max(yhat + q_lo * scale, 0.0)
        hi = max(yhat + q_hi * scale, lo)
        if h in horizons:
            results[h] = {
                "horizon": h,
                "yhat": round(yhat, 6),
                "lower_bound": round(lo, 6),
                "upper_bound": round(hi, 6),
                "interval_level": INTERVAL_LEVEL,
                "interval_method": BOOSTING_INTERVAL_METHOD,
            }
    return [results[h] for h in horizons]


# ----------------------------------------------------------- seasonal_ensemble
def fit_seasonal_ensemble(
    rows: list[TrainingRow], parameters: dict[str, Any], seed: int, cutoff: date
) -> FittedModelArtifact:
    """weight x seasonal_naive + (1 - weight) x component count model.

    Parameters (clipped to contract ranges):
      weight in [0, 1] (default 0.5, chosen on validation only),
      component_adapter in ENSEMBLE_COMPONENT_CANDIDATES.

    The component fit is resolved via ``execution.MODEL_FITS.get`` at fit time
    (never at import time) so registration order cannot create a cycle.
    """
    weight = _clip(float(parameters.get("weight", 0.5)), 0.0, 1.0)
    component = str(parameters.get("component_adapter", "negbin_dglm.v1"))
    if component not in ENSEMBLE_COMPONENT_CANDIDATES:
        component = "negbin_dglm.v1"
    frequency = _infer_frequency(rows, parameters)
    usable = [r for r in rows if r.period_start <= cutoff]
    sn_artifact = execution.fit_baseline("seasonal_naive", usable, {}, seed, cutoff, frequency)
    component_fit = execution.MODEL_FITS.get(component)
    if component_fit is None:
        raise ValueError(f"UNKNOWN_COMPONENT:{component}")
    component_artifact = component_fit(usable, {}, seed, cutoff)
    state = {
        "model": SEASONAL_ENSEMBLE_ID,
        "weight": weight,
        "component_adapter": component,
        "frequency": frequency,
        "seasonal_naive_artifact": asdict(sn_artifact),
        "component_artifact": asdict(component_artifact),
    }
    return make_artifact(SEASONAL_ENSEMBLE_ID, seed, state, usable)


def _passthrough(pred: dict[str, Any]) -> dict[str, Any]:
    return {
        "horizon": pred["horizon"],
        "yhat": pred["yhat"],
        "lower_bound": pred["lower_bound"],
        "upper_bound": pred["upper_bound"],
        "interval_level": pred["interval_level"],
        "interval_method": pred["interval_method"],
    }


def predict_seasonal_ensemble(
    fitted: FittedModelArtifact, horizons: list[int], issue_period: date
) -> list[dict[str, Any]]:
    """Weighted combination of component predictions and intervals. A NULL
    component degenerates to the other component (pass-through); both NULL
    yields a NULL prediction."""
    st = fitted.state
    weight = float(st["weight"])
    sn_artifact = FittedModelArtifact(**st["seasonal_naive_artifact"])
    component_artifact = FittedModelArtifact(**st["component_artifact"])
    sn_preds = {
        int(p["horizon"]): p
        for p in execution.predict_baseline(sn_artifact, horizons, issue_period)
    }
    component_predict = execution.MODEL_PREDICTS.get(component_artifact.adapter_id)
    comp_preds = (
        {int(p["horizon"]): p for p in component_predict(component_artifact, horizons, issue_period)}
        if component_predict is not None
        else {}
    )
    out = []
    for h in horizons:
        sn_p = sn_preds.get(h)
        comp_p = comp_preds.get(h)
        sn_ok = sn_p is not None and sn_p.get("yhat") is not None
        comp_ok = comp_p is not None and comp_p.get("yhat") is not None
        if sn_ok and comp_ok and sn_p is not None and comp_p is not None:
            yhat = weight * float(sn_p["yhat"]) + (1.0 - weight) * float(comp_p["yhat"])
            lo = weight * float(sn_p["lower_bound"]) + (1.0 - weight) * float(comp_p["lower_bound"])
            hi = weight * float(sn_p["upper_bound"]) + (1.0 - weight) * float(comp_p["upper_bound"])
            lo = max(lo, 0.0)
            out.append(
                {
                    "horizon": h,
                    "yhat": round(max(yhat, 0.0), 6),
                    "lower_bound": round(lo, 6),
                    "upper_bound": round(max(hi, lo), 6),
                    "interval_level": INTERVAL_LEVEL,
                    "interval_method": ENSEMBLE_INTERVAL_METHOD,
                }
            )
        elif sn_ok and sn_p is not None:
            out.append(_passthrough(sn_p))
        elif comp_ok and comp_p is not None:
            out.append(_passthrough(comp_p))
        else:
            out.append(_null_prediction(h, NULL_ALL_COMPONENTS))
    return out


# ------------------------------------------------------------------- registry
def register_extended_models() -> None:
    """Inject the four extended adapters into the execution dispatch tables.
    Additive (baselines and existing models untouched) and idempotent."""
    execution.MODEL_FITS[SEASONAL_NEGBIN_GLM_ID] = fit_seasonal_negbin_glm
    execution.MODEL_FITS[FOURIER_NEGBIN_GLM_ID] = fit_fourier_negbin_glm
    execution.MODEL_FITS[LAGGED_COUNT_BOOSTING_ID] = fit_lagged_count_boosting
    execution.MODEL_FITS[SEASONAL_ENSEMBLE_ID] = fit_seasonal_ensemble
    execution.MODEL_PREDICTS[SEASONAL_NEGBIN_GLM_ID] = predict_seasonal_negbin_glm
    execution.MODEL_PREDICTS[FOURIER_NEGBIN_GLM_ID] = predict_fourier_negbin_glm
    execution.MODEL_PREDICTS[LAGGED_COUNT_BOOSTING_ID] = predict_lagged_count_boosting
    execution.MODEL_PREDICTS[SEASONAL_ENSEMBLE_ID] = predict_seasonal_ensemble
