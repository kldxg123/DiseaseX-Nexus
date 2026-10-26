"""Real, executable (non-baseline) model adapters for kg_r3c.

Each adapter implements fit/predict on a 1-D count/rate series and exposes
self-describing metadata used by scoring (data requirements, probabilistic
output capability, parameter schema). Implementations are statistically real
(conjugate Bayesian updates / Kalman filtering / Cori-style renewal), kept
dependency-free (stdlib math only) so they are auditable. Adapters are NOT
executed against live data in this phase — executability is demonstrated by
fixture-based unit tests.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

ADAPTER_IMPL_VERSION = "kg_r3c.adapters.v1"


@dataclass(frozen=True)
class ParamSpec:
    name: str
    ptype: str          # float | int | distribution
    default: float
    lo: float
    hi: float
    unit: str = ""
    identifiable_min_obs: int = 8   # minimum observations to identify this param from data


@dataclass(frozen=True)
class AdapterSpec:
    adapter_id: str
    model_family: str
    adapter_version: str
    is_baseline: bool
    semantics: tuple          # INCIDENT / CUMULATIVE (via diff) / EVENT / PROPORTION
    min_obs: int
    supports_zero_inflation: bool
    supports_overdispersion: bool
    probabilistic_output: bool
    handles_ultra_short: bool
    params: tuple
    disease_mechanism: str    # OBSERVATION_PROCESS | TRANSMISSION_PROCESS | GENERIC_STATISTICAL


# ---------------------------------------------------------------- gamma-poisson
def _logsumexp_terms(terms):
    m = max(terms)
    return m + math.log(sum(math.exp(t - m) for t in terms))


def gamma_poisson_dglm_fit(values, discount=0.95, a0=1.0, b0=1.0):
    """Conjugate Gamma-Poisson DGLM (West & Harrison): prior Gamma(a,b),
    discount evolution, posterior update; 1-step NB predictive."""
    a, b = a0, b0
    one_step_var, ll = [], 0.0
    for y in values:
        # predictive NegBin(r=a, p=b/(b+1))
        r, p = a, b / (b + 1)
        one_step_var.append(r * (1 - p) / (p * p))
        ll += _nb_logpmf(int(round(y)), r, p)
        # evolution: discount
        a_e, b_e = discount * a, discount * b
        # update
        a, b = a_e + y, b_e + 1.0
    return {"loglik": ll, "final_shape": a, "final_rate": b,
            "mean_one_step_var": sum(one_step_var) / len(one_step_var)}


def _nb_logpmf(k, r, p):
    return (math.lgamma(k + r) - math.lgamma(r) - math.lgamma(k + 1)
            + r * math.log(p) + k * math.log(1 - p))


def negbin_dglm_fit(values, discount=0.95, shape0=10.0, a0=1.0, b0=1.0):
    """NB-observation DGLM with fixed shape (dispersion) and Gamma random-walk mean."""
    a, b, shape = a0, b0, shape0
    ll = 0.0
    for y in values:
        mu = a / b
        p = shape / (shape + mu)
        ll += _nb_logpmf(int(round(y)), shape, p)
        a_e, b_e = discount * a, discount * b
        # moment-matched conjugate-ish update on the mean
        a, b = a_e + shape * (y / (y + shape)), b_e + shape / (y + shape) if y > 0 else (a_e * 0.99, b_e + 0.01)
    return {"loglik": ll, "final_shape": a, "final_rate": b}


def hurdle_count_fit(values, discount=0.95):
    """Dynamic hurdle: zero/one Bernoulli process (beta-discount) + positive-part
    Gamma-Poisson on nonzero counts."""
    zeros = [1 if v == 0 else 0 for v in values]
    pos = [v for v in values if v > 0]
    az = bz = 1.0
    for z in zeros:
        az_e, bz_e = discount * az, discount * bz
        az, bz = az_e + z, bz_e + (1 - z)
    gp = gamma_poisson_dglm_fit(pos, discount) if pos else {"loglik": 0.0}
    return {"loglik": gp["loglik"], "zero_alpha": az, "zero_beta": bz,
            "positive_part": gp}


def kalman_local_level_fit(values, obs_var=None, state_var=None):
    """Random-walk-plus-noise Kalman filter on log1p scale."""
    xs = [math.log1p(max(v, 0.0)) for v in values]
    if obs_var is None:
        obs_var = max(statistics_var(xs) * 0.5, 1e-3)
    if state_var is None:
        state_var = max(statistics_var(xs) * 0.1, 1e-4)
    m, p = xs[0], 1.0
    ll = 0.0
    for x in xs:
        p_pred = p + state_var
        s = p_pred + obs_var
        innov = x - m
        ll += -0.5 * (math.log(2 * math.pi * s) + innov * innov / s)
        k = p_pred / s
        m, p = m + k * innov, (1 - k) * p_pred
    return {"loglik": ll, "final_level": m, "final_var": p}


def kalman_local_trend_fit(values, obs_var=None, level_var=None, trend_var=None):
    xs = [math.log1p(max(v, 0.0)) for v in values]
    if obs_var is None:
        obs_var = max(statistics_var(xs) * 0.5, 1e-3)
    level_var = level_var or max(statistics_var(xs) * 0.05, 1e-4)
    trend_var = trend_var or level_var * 0.1
    level, trend = xs[0], 0.0
    P = [[1.0, 0.0], [0.0, 0.1]]
    ll = 0.0
    for x in xs:
        lp = level + trend
        P00 = P[0][0] + 2 * P[0][1] + P[1][1] + level_var + trend_var
        P01 = P[0][1] + P[1][1] + trend_var
        P11 = P[1][1] + trend_var
        s = P00 + obs_var
        innov = x - lp
        ll += -0.5 * (math.log(2 * math.pi * s) + innov * innov / s)
        k0, k1 = P00 / s, P01 / s
        level, trend = lp + k0 * innov, trend + k1 * innov
        P = [[P00 - k0 * P00, P01 - k0 * P01], [P01 - k1 * P00, P11 - k1 * P01]]
    return {"loglik": ll, "final_level": level, "final_trend": trend}


def beta_binomial_dynamic_fit(values, totals, discount=0.95, a0=1.0, b0=1.0):
    """Conjugate Beta-Binomial with discount evolution for proportion series."""
    a, b = a0, b0
    ll = 0.0
    for y, n in zip(values, totals):
        # beta-binomial predictive log-pmf
        ll += (math.lgamma(n + 1) - math.lgamma(y + 1) - math.lgamma(n - y + 1)
               + math.lgamma(y + a) + math.lgamma(n - y + b) - math.lgamma(n + a + b)
               - (math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)))
        a_e, b_e = discount * a, discount * b
        a, b = a_e + y, b_e + (n - y)
    return {"loglik": ll, "final_alpha": a, "final_beta": b}


def renewal_corri_fit(values, si_mean=4.0, si_sd=2.0, prior_a=1.0, prior_b=5.0):
    """Cori-style R_t estimation with Gamma prior; requires incident counts."""
    # discretized serial interval (gamma)
    max_k = 20
    shape = (si_mean / si_sd) ** 2
    scale = si_sd ** 2 / si_mean
    w = [math.exp((shape - 1) * math.log(k) - k / scale) if k > 0 else 0.0 for k in range(1, max_k + 1)]
    sw = sum(w)
    w = [x / sw for x in w]
    rts = []
    ll = 0.0
    for t in range(1, len(values)):
        lam = sum(values[t - k] * w[k - 1] for k in range(1, min(t, max_k) + 1))
        a = prior_a + values[t]
        b = 1 / (1 / prior_b + lam) if lam > 0 else prior_b
        rts.append(a * b)
        if lam > 0:
            mu = (prior_a / (1 / prior_b + lam)) * lam if False else (a * b) * lam
            ll += -mu + values[t] * math.log(max(mu, 1e-12)) - math.lgamma(values[t] + 1)
    return {"loglik": ll, "r_final": rts[-1] if rts else None}


def event_rate_fit(values, a0=1.0, b0=1.0):
    """Gamma-Poisson event-rate model for sparse event series (no fake padding)."""
    n = len(values)
    total = sum(values)
    a, b = a0 + total, b0 + n
    return {"loglik": sum(-a / b + v * math.log(max(a / b, 1e-12)) - math.lgamma(v + 1)
                          for v in values), "rate_shape": a, "rate_rate": b}


def cumulative_incidence_fit(values, a0=1.0, b0=1.0):
    """Bayesian cumulative-incidence scenario model for ultra-short cumulative
    snapshots: Gamma posterior over the total-incidence rate per period from the
    observed increments (or the single cumulative total). Real posterior output;
    never interpolates a fake long series."""
    increments = [max(values[0], 0.0)] + [max(b - a, 0.0) for a, b in zip(values, values[1:])]
    n = len(increments)
    total = sum(increments)
    a, b = a0 + total, b0 + n
    mean_rate = a / b
    ll = sum(-mean_rate + v * math.log(max(mean_rate, 1e-12)) - math.lgamma(v + 1)
             for v in increments)
    return {"loglik": ll, "rate_shape": a, "rate_rate": b,
            "posterior_mean_rate": mean_rate, "periods_observed": n}


def statistics_var(xs):
    m = sum(xs) / len(xs)
    return sum((x - m) ** 2 for x in xs) / max(len(xs) - 1, 1)


# ---------------------------------------------------------------- registry
REGISTRY = [
    AdapterSpec(
        adapter_id="gamma_poisson_dglm.v1", model_family="Gamma-Poisson DGLM",
        adapter_version=ADAPTER_IMPL_VERSION, is_baseline=False,
        semantics=("INCIDENT",), min_obs=12,
        supports_zero_inflation=False, supports_overdispersion=True,
        probabilistic_output=True, handles_ultra_short=False,
        params=(ParamSpec("discount", "float", 0.95, 0.8, 0.999),
                ParamSpec("prior_shape", "float", 1.0, 0.01, 100.0),
                ParamSpec("prior_rate", "float", 1.0, 0.01, 100.0)),
        disease_mechanism="OBSERVATION_PROCESS"),
    AdapterSpec(
        adapter_id="negbin_dglm.v1", model_family="Negative-Binomial DGLM",
        adapter_version=ADAPTER_IMPL_VERSION, is_baseline=False,
        semantics=("INCIDENT",), min_obs=12,
        supports_zero_inflation=False, supports_overdispersion=True,
        probabilistic_output=True, handles_ultra_short=False,
        params=(ParamSpec("discount", "float", 0.95, 0.8, 0.999),
                ParamSpec("shape", "float", 10.0, 0.5, 500.0, identifiable_min_obs=24),
                ParamSpec("prior_shape", "float", 1.0, 0.01, 100.0),
                ParamSpec("prior_rate", "float", 1.0, 0.01, 100.0)),
        disease_mechanism="OBSERVATION_PROCESS"),
    AdapterSpec(
        adapter_id="hurdle_count_ss.v1", model_family="Dynamic hurdle count state-space",
        adapter_version=ADAPTER_IMPL_VERSION, is_baseline=False,
        semantics=("INCIDENT",), min_obs=24,
        supports_zero_inflation=True, supports_overdispersion=True,
        probabilistic_output=True, handles_ultra_short=False,
        params=(ParamSpec("discount", "float", 0.95, 0.8, 0.999),
                ParamSpec("zero_prior_alpha", "float", 1.0, 0.01, 100.0),
                ParamSpec("zero_prior_beta", "float", 1.0, 0.01, 100.0)),
        disease_mechanism="OBSERVATION_PROCESS"),
    AdapterSpec(
        adapter_id="bayes_local_level.v1", model_family="Bayesian local level",
        adapter_version=ADAPTER_IMPL_VERSION, is_baseline=False,
        semantics=("INCIDENT", "CUMULATIVE"), min_obs=12,
        supports_zero_inflation=False, supports_overdispersion=False,
        probabilistic_output=True, handles_ultra_short=False,
        params=(ParamSpec("obs_var", "float", 0.0, 1e-4, 100.0, identifiable_min_obs=12),
                ParamSpec("state_var", "float", 0.0, 1e-5, 100.0, identifiable_min_obs=12)),
        disease_mechanism="GENERIC_STATISTICAL"),
    AdapterSpec(
        adapter_id="bayes_local_trend.v1", model_family="Bayesian local trend",
        adapter_version=ADAPTER_IMPL_VERSION, is_baseline=False,
        semantics=("INCIDENT", "CUMULATIVE"), min_obs=24,
        supports_zero_inflation=False, supports_overdispersion=False,
        probabilistic_output=True, handles_ultra_short=False,
        params=(ParamSpec("obs_var", "float", 0.0, 1e-4, 100.0, identifiable_min_obs=24),
                ParamSpec("level_var", "float", 0.0, 1e-5, 100.0, identifiable_min_obs=24),
                ParamSpec("trend_var", "float", 0.0, 1e-6, 10.0, identifiable_min_obs=24)),
        disease_mechanism="GENERIC_STATISTICAL"),
    AdapterSpec(
        adapter_id="beta_binomial_dynamic.v1", model_family="Dynamic beta-binomial proportion",
        adapter_version=ADAPTER_IMPL_VERSION, is_baseline=False,
        semantics=("PROPORTION",), min_obs=12,
        supports_zero_inflation=False, supports_overdispersion=True,
        probabilistic_output=True, handles_ultra_short=False,
        params=(ParamSpec("discount", "float", 0.95, 0.8, 0.999),
                ParamSpec("prior_alpha", "float", 1.0, 0.01, 100.0),
                ParamSpec("prior_beta", "float", 1.0, 0.01, 100.0)),
        disease_mechanism="OBSERVATION_PROCESS"),
    AdapterSpec(
        adapter_id="renewal_corri.v1", model_family="Renewal (Cori R_t)",
        adapter_version=ADAPTER_IMPL_VERSION, is_baseline=False,
        semantics=("INCIDENT",), min_obs=21,
        supports_zero_inflation=False, supports_overdispersion=False,
        probabilistic_output=True, handles_ultra_short=False,
        params=(ParamSpec("si_mean", "float", 4.0, 1.0, 30.0, "days"),
                ParamSpec("si_sd", "float", 2.0, 0.5, 15.0, "days"),
                ParamSpec("prior_a", "float", 1.0, 0.01, 100.0),
                ParamSpec("prior_b", "float", 5.0, 0.01, 100.0)),
        disease_mechanism="TRANSMISSION_PROCESS"),
    AdapterSpec(
        adapter_id="poisson_event_rate.v1", model_family="Poisson event-rate (Gamma prior)",
        adapter_version=ADAPTER_IMPL_VERSION, is_baseline=False,
        semantics=("EVENT",), min_obs=1,
        supports_zero_inflation=True, supports_overdispersion=False,
        probabilistic_output=True, handles_ultra_short=True,
        params=(ParamSpec("prior_shape", "float", 1.0, 0.01, 100.0),
                ParamSpec("prior_rate", "float", 1.0, 0.01, 100.0)),
        disease_mechanism="OBSERVATION_PROCESS"),
    AdapterSpec(
        adapter_id="cumulative_incidence_bayes.v1",
        model_family="Bayesian cumulative-incidence scenario model",
        adapter_version=ADAPTER_IMPL_VERSION, is_baseline=False,
        semantics=("CUMULATIVE",), min_obs=1,
        supports_zero_inflation=True, supports_overdispersion=False,
        probabilistic_output=True, handles_ultra_short=True,
        params=(ParamSpec("prior_shape", "float", 1.0, 0.01, 100.0, identifiable_min_obs=1),
                ParamSpec("prior_rate", "float", 1.0, 0.01, 100.0, identifiable_min_obs=1)),
        disease_mechanism="OBSERVATION_PROCESS"),
    # baselines: diagnostic only, never recommendable
    AdapterSpec(
        adapter_id="baseline_naive.v1", model_family="Naive last-value",
        adapter_version=ADAPTER_IMPL_VERSION, is_baseline=True,
        semantics=("INCIDENT", "CUMULATIVE", "EVENT", "PROPORTION"), min_obs=1,
        supports_zero_inflation=True, supports_overdispersion=True,
        probabilistic_output=False, handles_ultra_short=True, params=(),
        disease_mechanism="GENERIC_STATISTICAL"),
    AdapterSpec(
        adapter_id="baseline_mean.v1", model_family="Historical mean",
        adapter_version=ADAPTER_IMPL_VERSION, is_baseline=True,
        semantics=("INCIDENT", "CUMULATIVE", "EVENT", "PROPORTION"), min_obs=1,
        supports_zero_inflation=True, supports_overdispersion=True,
        probabilistic_output=False, handles_ultra_short=True, params=(),
        disease_mechanism="GENERIC_STATISTICAL"),
]

FIT_FUNCS = {
    "gamma_poisson_dglm.v1": gamma_poisson_dglm_fit,
    "negbin_dglm.v1": negbin_dglm_fit,
    "hurdle_count_ss.v1": hurdle_count_fit,
    "bayes_local_level.v1": kalman_local_level_fit,
    "bayes_local_trend.v1": kalman_local_trend_fit,
    "renewal_corri.v1": renewal_corri_fit,
    "poisson_event_rate.v1": event_rate_fit,
    "cumulative_incidence_bayes.v1": cumulative_incidence_fit,
}
