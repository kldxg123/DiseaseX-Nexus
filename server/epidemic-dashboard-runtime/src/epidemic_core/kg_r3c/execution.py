"""Executable forecast adapter contract for the KG dual-dataset feedback loop.

This module upgrades the R3C fit-only adapters (``kg_r3c.adapters``) into a
full fit/predict execution contract used by shadow executions:

    fit(training_rows, parameters, seed, cutoff) -> FittedModelArtifact
    predict(fitted_model, horizons, issue_period) -> list[ForecastDistribution]

Everything is dependency-free (stdlib ``math`` only) so it runs in the
dashboard runtime environment. All quantiles are computed deterministically
from exact predictive pmf recurrences, so identical input + seed always
reproduces identical output. Missing observations are never filled, zeroed
or interpolated: training skips them (state still evolves), and baselines
that lack a required lag return NULL predictions instead of fabricated
values.
"""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any

EXECUTION_IMPL_VERSION = "kg_r3c.execution.v1"
INTERVAL_LEVEL = 0.95
NS = uuid.UUID("c87f8a95-7d2c-4f3e-9b1a-2e5d6a8b0c1f")

BASELINE_IDS = ("last_value", "rolling_mean", "drift", "seasonal_naive")
SEASONAL_NAIVE_NOT_ELIGIBLE = "NOT_ELIGIBLE_INSUFFICIENT_TWO_CYCLES"


def stable_uuid(kind: str, key: str) -> uuid.UUID:
    return uuid.uuid5(NS, f"{kind}:{key}")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_json(payload: Any) -> str:
    return sha256_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str))


# ------------------------------------------------------------------ contract
@dataclass(frozen=True)
class TrainingRow:
    period_start: date
    period_end: date | None
    value: float | None  # None = missing observation, never filled
    observation_id: str | None = None
    revision: int = 1


@dataclass(frozen=True)
class FittedModelArtifact:
    adapter_id: str
    adapter_version: str
    seed: int
    state: dict[str, Any]
    train_row_count: int
    train_sha256: str
    artifact_sha256: str


@dataclass(frozen=True)
class ForecastDistribution:
    run_id: str
    profile_id: str
    snapshot_id: str
    dataset_sha256: str
    recommendation_id: str
    execution_contract_id: str
    adapter_id: str
    adapter_version: str
    parameter_bundle_id: str
    issue_period: date
    data_through_period: date
    feature_max_period: date
    target_period: date
    horizon: int
    yhat: float | None
    lower_bound: float | None
    upper_bound: float | None
    interval_level: float
    seed: int
    model_artifact_sha256: str
    interval_method: str = "EXACT_PREDICTIVE_PMF"


def train_sha(rows: list[TrainingRow]) -> str:
    lines = [
        f"{r.period_start.isoformat()}|{r.period_end.isoformat() if r.period_end else ''}|"
        f"{'' if r.value is None else repr(float(r.value))}|{r.observation_id or ''}|{r.revision}"
        for r in rows
    ]
    return sha256_text("\n".join(lines) + "\n")


def make_artifact(adapter_id: str, seed: int, state: dict[str, Any], rows: list[TrainingRow]) -> FittedModelArtifact:
    sha = sha256_json(
        {
            "adapter_id": adapter_id,
            "adapter_version": EXECUTION_IMPL_VERSION,
            "seed": seed,
            "state": state,
            "train_sha256": train_sha(rows),
        }
    )
    return FittedModelArtifact(
        adapter_id=adapter_id,
        adapter_version=EXECUTION_IMPL_VERSION,
        seed=seed,
        state=state,
        train_row_count=len(rows),
        train_sha256=train_sha(rows),
        artifact_sha256=sha,
    )


# --------------------------------------------------------------- NB utilities
def nb_logpmf(k: int, r: float, p: float) -> float:
    """Negative-binomial log pmf: P(Y=k) for failures-count parameterisation
    with shape r and success prob p (mean = r*(1-p)/p)."""
    if k < 0:
        return -math.inf
    return math.lgamma(k + r) - math.lgamma(r) - math.lgamma(k + 1) + r * math.log(p) + k * math.log1p(-p)


def nb_quantiles(mean: float, shape: float, level: float = INTERVAL_LEVEL) -> tuple[float, float]:
    """Deterministic (lower, upper) central-quantiles of an NB predictive with
    the given mean and shape, computed by exact pmf recurrence."""
    mean = max(mean, 0.0)
    shape = max(shape, 1e-6)
    p = shape / (shape + mean) if mean > 0 else 1.0
    lo_q = (1.0 - level) / 2.0
    hi_q = 1.0 - lo_q
    # p(0)
    log_p0 = shape * math.log(p)
    probs = [math.exp(log_p0)]
    cum = probs[0]
    k = 0
    lo = hi = None
    limit = max(int(mean + 12.0 * math.sqrt(mean + mean * mean / shape)) + 10, 60)
    while k < limit:
        if lo is None and cum >= lo_q:
            lo = k
        if hi is None and cum >= hi_q:
            hi = k
            break
        # p(k+1)/p(k) = (k+r)/(k+1) * (1-p)
        probs.append(probs[-1] * (k + shape) / (k + 1) * (1.0 - p))
        cum += probs[-1]
        k += 1
    if lo is None:
        lo = 0
    if hi is None:
        hi = k
    return float(lo), float(hi)


def hurdle_quantiles(
    p_occ: float, mean_pos: float, shape_pos: float, level: float = INTERVAL_LEVEL
) -> tuple[float, float]:
    """Central quantiles of a hurdle predictive: P(Y=0)=1-p_occ, positive part
    zero-truncated NB(mean_pos, shape_pos). Deterministic pmf walk."""
    p_occ = min(max(p_occ, 0.0), 1.0)
    mean_pos = max(mean_pos, 1e-9)
    shape_pos = max(shape_pos, 1e-6)
    p = shape_pos / (shape_pos + mean_pos)
    log_p0 = shape_pos * math.log(p)
    p0 = math.exp(log_p0)
    zt_norm = max(1.0 - p0, 1e-12)
    lo_q = (1.0 - level) / 2.0
    hi_q = 1.0 - lo_q
    cum = 1.0 - p_occ  # P(Y=0)
    lo = hi = None
    if cum >= lo_q and lo is None:
        lo = 0
    if cum >= hi_q and hi is None:
        hi = 0
    prob = p0
    k = 0
    limit = max(int(p_occ * mean_pos * 3 + 12.0 * math.sqrt(mean_pos + mean_pos**2 / shape_pos)) + 60, 120)
    while k < limit:
        if k >= 1:
            pass
        k += 1
        # NB pmf at k via recurrence from p0
        prob = prob * (k - 1 + shape_pos) / k * (1.0 - p)
        cum += p_occ * prob / zt_norm
        if lo is None and cum >= lo_q:
            lo = k
        if hi is None and cum >= hi_q:
            hi = k
            break
    return float(lo or 0), float(hi or 0)


def _observed(values: list[float | None]) -> list[float]:
    return [v for v in values if v is not None]


# ------------------------------------------------------------- negbin_dglm.v1
def fit_negbin_dglm(
    rows: list[TrainingRow], parameters: dict[str, Any], seed: int, cutoff: date
) -> FittedModelArtifact:
    """NB-observation DGLM: Gamma random-walk on the mean with fixed NB shape.

    Parameters (with bundle / registry defaults, clipped to allowed ranges):
      discount in [0.8, 0.999], shape in [0.5, 500], prior_shape/rate in (0, 100].
    Missing observations keep the state evolving (discount) without an update.
    """
    discount = min(max(float(parameters.get("discount", 0.95)), 0.8), 0.999)
    shape = min(max(float(parameters.get("shape", 10.0)), 0.5), 500.0)
    a = min(max(float(parameters.get("prior_shape", 1.0)), 0.01), 100.0)
    b = min(max(float(parameters.get("prior_rate", 1.0)), 0.01), 100.0)
    usable = [r for r in rows if r.period_start <= cutoff]
    for row in usable:
        a_e, b_e = discount * a, discount * b
        if row.value is None:
            a, b = a_e, b_e  # missing stays missing: evolve only, never fill
            continue
        y = max(float(row.value), 0.0)
        a, b = a_e + shape * (y / (y + shape)), b_e + shape / (y + shape)
    state = {
        "posterior_shape": a,
        "posterior_rate": b,
        "nb_shape": shape,
        "discount": discount,
        "model": "negbin_dglm.v1",
    }
    return make_artifact("negbin_dglm.v1", seed, state, usable)


def predict_negbin_dglm(fitted: FittedModelArtifact, horizons: list[int], issue_period: date) -> list[dict[str, Any]]:
    """Multi-step predictive: state discounted forward h steps (mean preserved,
    variance grows); total variance = observation NB variance + posterior
    uncertainty of the latent mean, moment-matched back to an NB."""
    a = float(fitted.state["posterior_shape"])
    b = float(fitted.state["posterior_rate"])
    shape = float(fitted.state["nb_shape"])
    discount = float(fitted.state["discount"])
    out = []
    for h in horizons:
        a_h = a * (discount**h)
        b_h = b * (discount**h)
        mu = max(a_h / b_h, 0.0)
        var_obs = mu + mu * mu / shape
        var_state = mu * mu / max(a_h, 1e-9)
        var_total = var_obs + var_state
        r_eff = mu * mu / max(var_total - mu, 1e-9) if var_total > mu else 1e6
        lo, hi = nb_quantiles(mu, r_eff)
        out.append(
            {
                "horizon": h,
                "yhat": round(mu, 6),
                "lower_bound": lo,
                "upper_bound": hi,
                "interval_level": INTERVAL_LEVEL,
                "interval_method": "EXACT_PREDICTIVE_PMF",
            }
        )
    return out


# ----------------------------------------------------------- hurdle_count_ss.v1
def fit_hurdle_count_ss(
    rows: list[TrainingRow], parameters: dict[str, Any], seed: int, cutoff: date
) -> FittedModelArtifact:
    """Dynamic hurdle state-space model.

    Occurrence process: Beta-Bernoulli with discount evolution on the
    zero/nonzero indicator. Intensity process: Gamma-Poisson with discount
    evolution over positive counts only. Missing observations evolve both
    states without updating.
    """
    discount = min(max(float(parameters.get("discount", 0.95)), 0.8), 0.999)
    az = min(max(float(parameters.get("zero_prior_alpha", 1.0)), 0.01), 100.0)
    bz = min(max(float(parameters.get("zero_prior_beta", 1.0)), 0.01), 100.0)
    ap = min(max(float(parameters.get("prior_shape", 1.0)), 0.01), 100.0)
    bp = min(max(float(parameters.get("prior_rate", 1.0)), 0.01), 100.0)
    usable = [r for r in rows if r.period_start <= cutoff]
    n_positive = 0
    for row in usable:
        az_e, bz_e = discount * az, discount * bz
        ap_e, bp_e = discount * ap, discount * bp
        if row.value is None:
            az, bz, ap, bp = az_e, bz_e, ap_e, bp_e
            continue
        y = max(float(row.value), 0.0)
        if y > 0:
            az, bz = az_e + 1.0, bz_e
            ap, bp = ap_e + y, bp_e + 1.0
            n_positive += 1
        else:
            az, bz = az_e, bz_e + 1.0
            ap, bp = ap_e, bp_e
    state = {
        "occ_alpha": az,
        "occ_beta": bz,
        "pos_shape": ap,
        "pos_rate": bp,
        "discount": discount,
        "n_positive": n_positive,
        "model": "hurdle_count_ss.v1",
    }
    return make_artifact("hurdle_count_ss.v1", seed, state, usable)


def predict_hurdle_count_ss(
    fitted: FittedModelArtifact, horizons: list[int], issue_period: date
) -> list[dict[str, Any]]:
    """Multi-step hurdle predictive: occurrence probability and positive
    intensity discounted forward; zero-truncated NB positive part."""
    az = float(fitted.state["occ_alpha"])
    bz = float(fitted.state["occ_beta"])
    ap = float(fitted.state["pos_shape"])
    bp = float(fitted.state["pos_rate"])
    discount = float(fitted.state["discount"])
    out = []
    for h in horizons:
        az_h, bz_h = az * (discount**h), bz * (discount**h)
        ap_h, bp_h = ap * (discount**h), bp * (discount**h)
        p_occ = az_h / max(az_h + bz_h, 1e-12)
        mu_pos = max(ap_h / bp_h, 1e-9)
        # NB shape for positive part: posterior uncertainty + poisson noise
        var_pos = mu_pos + mu_pos * mu_pos / max(ap_h, 1e-9)
        r_pos = mu_pos * mu_pos / max(var_pos - mu_pos, 1e-9) if var_pos > mu_pos else 1e6
        p0 = math.exp(r_pos * math.log(r_pos / (r_pos + mu_pos)))
        zt_mean = mu_pos / max(1.0 - p0, 1e-12)
        yhat = p_occ * zt_mean
        lo, hi = hurdle_quantiles(p_occ, mu_pos, r_pos)
        out.append(
            {
                "horizon": h,
                "yhat": round(max(yhat, 0.0), 6),
                "lower_bound": lo,
                "upper_bound": hi,
                "interval_level": INTERVAL_LEVEL,
                "interval_method": "EXACT_PREDICTIVE_PMF",
                "zero_probability": round(1.0 - p_occ, 6),
            }
        )
    return out


# ------------------------------------------------------------------ baselines
def _residual_sigma(values: list[float], errors: list[float]) -> float:
    if not errors:
        return math.sqrt(max(sum(values) / max(len(values), 1), 1.0))
    m = sum(errors) / len(errors)
    var = sum((e - m) ** 2 for e in errors) / max(len(errors) - 1, 1)
    return max(math.sqrt(var), 1e-6)


def seasonal_naive_eligible(rows: list[TrainingRow], frequency: str, lag: int) -> str | None:
    """A seasonal-naive baseline honestly requires at least two full cycles of
    history to be trained *and* evaluated; otherwise it is not eligible and
    must return NULL rather than fabricating values."""
    n = len([r for r in rows if r.value is not None])
    if n < 2 * lag:
        return SEASONAL_NAIVE_NOT_ELIGIBLE
    return None


def fit_baseline(
    baseline_id: str, rows: list[TrainingRow], parameters: dict[str, Any], seed: int, cutoff: date, frequency: str
) -> FittedModelArtifact:
    usable = [r for r in rows if r.period_start <= cutoff]
    values = [r.value for r in usable]
    obs = _observed(values)
    state: dict[str, Any] = {"model": baseline_id, "frequency": frequency}
    if baseline_id == "last_value":
        state["last"] = obs[-1] if obs else None
    elif baseline_id == "rolling_mean":
        window = int(parameters.get("window", 4))
        state["window"] = window
        state["mean"] = (sum(obs[-window:]) / len(obs[-window:])) if len(obs) >= 1 else None
        state["eligible"] = len(obs) >= 1
    elif baseline_id == "drift":
        if len(obs) >= 2:
            state["last"] = obs[-1]
            state["slope"] = (obs[-1] - obs[0]) / (len(obs) - 1)
        else:
            state["last"] = obs[-1] if obs else None
            state["slope"] = 0.0
    elif baseline_id == "seasonal_naive":
        lag = int(parameters.get("season_lag", 12 if frequency == "month" else 52))
        state["season_lag"] = lag
        state["not_eligible"] = seasonal_naive_eligible(usable, frequency, lag)
        # seasonal value lookup happens at predict time from full train tail
        state["tail"] = [v for v in values]  # may contain None; None -> NULL prediction
    # empirical 1-step residual sigma for honest baseline intervals
    errors = []
    for prev, cur in zip(obs, obs[1:], strict=False):
        errors.append(cur - prev)
    state["sigma"] = _residual_sigma(obs, errors) if obs else None
    return make_artifact(baseline_id, seed, state, usable)


def predict_baseline(fitted: FittedModelArtifact, horizons: list[int], issue_period: date) -> list[dict[str, Any]]:
    st = fitted.state
    model = st["model"]
    if model == "seasonal_naive" and st.get("not_eligible"):
        return [
            {
                "horizon": h,
                "yhat": None,
                "lower_bound": None,
                "upper_bound": None,
                "interval_level": INTERVAL_LEVEL,
                "interval_method": "NONE_NOT_ELIGIBLE",
                "not_eligible": st["not_eligible"],
            }
            for h in horizons
        ]
    sigma = st.get("sigma")
    z = 1.959963984540054  # 95% normal quantile
    out = []
    for h in horizons:
        yhat: float | None
        if model == "last_value":
            yhat = st.get("last")
        elif model == "rolling_mean":
            yhat = st.get("mean")
        elif model == "drift":
            yhat = None if st.get("last") is None else st["last"] + h * st.get("slope", 0.0)
        elif model == "seasonal_naive":
            lag = int(st["season_lag"])
            tail = st.get("tail") or []
            idx = len(tail) - lag + (h - 1)
            yhat = tail[idx] if 0 <= idx < len(tail) else None
        else:  # pragma: no cover
            yhat = None
        if yhat is None:
            out.append(
                {
                    "horizon": h,
                    "yhat": None,
                    "lower_bound": None,
                    "upper_bound": None,
                    "interval_level": INTERVAL_LEVEL,
                    "interval_method": "NONE_MISSING_LAG",
                }
            )
            continue
        yhat = max(float(yhat), 0.0)
        half = z * (sigma or 0.0) * math.sqrt(h)
        out.append(
            {
                "horizon": h,
                "yhat": round(yhat, 6),
                "lower_bound": round(max(yhat - half, 0.0), 6),
                "upper_bound": round(yhat + half, 6),
                "interval_level": INTERVAL_LEVEL,
                "interval_method": "EMPIRICAL_RESIDUAL_NORMAL",
            }
        )
    return out


# ------------------------------------------------------------------ dispatch
MODEL_FITS = {
    "negbin_dglm.v1": fit_negbin_dglm,
    "hurdle_count_ss.v1": fit_hurdle_count_ss,
}
MODEL_PREDICTS = {
    "negbin_dglm.v1": predict_negbin_dglm,
    "hurdle_count_ss.v1": predict_hurdle_count_ss,
}


def fit_model(
    adapter_id: str, rows: list[TrainingRow], parameters: dict[str, Any], seed: int, cutoff: date, frequency: str
) -> FittedModelArtifact:
    if adapter_id in MODEL_FITS:
        return MODEL_FITS[adapter_id](rows, parameters, seed, cutoff)
    if adapter_id in BASELINE_IDS:
        return fit_baseline(adapter_id, rows, parameters, seed, cutoff, frequency)
    raise ValueError(f"UNKNOWN_ADAPTER:{adapter_id}")


def predict_model(fitted: FittedModelArtifact, horizons: list[int], issue_period: date) -> list[dict[str, Any]]:
    if fitted.adapter_id in MODEL_PREDICTS:
        return MODEL_PREDICTS[fitted.adapter_id](fitted, horizons, issue_period)
    if fitted.adapter_id in BASELINE_IDS:
        return predict_baseline(fitted, horizons, issue_period)
    raise ValueError(f"UNKNOWN_ADAPTER:{fitted.adapter_id}")


def baseline_not_eligible(fitted: FittedModelArtifact) -> str | None:
    return fitted.state.get("not_eligible")


def nb_deviance(y: float, mu: float, shape: float | None) -> float | None:
    """Poisson/NB deviance contribution, when applicable."""
    if mu is None or mu <= 0:
        return None
    y = max(float(y), 0.0)
    if shape is None:  # Poisson deviance
        if y == 0:
            return 2.0 * mu
        return 2.0 * (y * math.log(y / mu) - (y - mu))
    r = max(shape, 1e-9)
    if y == 0:
        return 2.0 * r * math.log((r + mu) / r)
    return 2.0 * (y * math.log(y / mu) - (y + r) * math.log((y + r) / (mu + r)))
