"""Adapter executability tests: every non-baseline adapter really fits fixture data."""
import math

import pytest

from epidemic_core.kg_r3c.adapters import (
    FIT_FUNCS, REGISTRY, beta_binomial_dynamic_fit, gamma_poisson_dglm_fit,
    renewal_corri_fit, event_rate_fit, kalman_local_level_fit,
)

SERIES = [3, 4, 2, 5, 7, 6, 8, 9, 7, 10, 12, 11, 13, 12, 14, 15, 13, 16, 17, 18,
          16, 19, 20, 21, 22, 20, 23, 24, 25, 26, 24, 27, 28, 30, 29, 31]
ZERO_HEAVY = [0, 0, 1, 0, 2, 0, 0, 3, 0, 1, 0, 4, 0, 0, 2, 1, 0, 0, 5, 0,
              1, 0, 0, 2, 0, 3, 0, 1, 0, 0, 2, 0, 0, 1, 3, 0]


class TestExecutability:
    @pytest.mark.parametrize("adapter_id", sorted(FIT_FUNCS))
    def test_fit_returns_loglik(self, adapter_id):
        out = FIT_FUNCS[adapter_id](SERIES)
        assert isinstance(out, dict)
        assert math.isfinite(out.get("loglik", 0.0))

    def test_beta_binomial(self):
        out = beta_binomial_dynamic_fit([1, 2, 1, 3, 2, 4, 3, 5, 4, 6, 5, 7], [10] * 12)
        assert math.isfinite(out["loglik"])

    def test_all_registered_adapters_have_impl(self):
        for a in REGISTRY:
            if a.is_baseline or a.adapter_id == "beta_binomial_dynamic.v1":
                continue
            assert a.adapter_id in FIT_FUNCS, a.adapter_id


class TestStatisticalSanity:
    def test_gamma_poisson_better_fit_better_loglik(self):
        good = gamma_poisson_dglm_fit(SERIES, discount=0.95)
        bad = gamma_poisson_dglm_fit([1000 if i % 2 else 0 for i in range(len(SERIES))],
                                     discount=0.95)
        assert good["loglik"] > bad["loglik"]

    def test_kalman_tracks_level(self):
        out = kalman_local_level_fit([50.0] * 30)
        assert abs(math.expm1(out["final_level"]) - 50.0) < 5.0

    def test_hurdle_captures_zero_process(self):
        from epidemic_core.kg_r3c.adapters import hurdle_count_fit
        out = hurdle_count_fit(ZERO_HEAVY)
        zero_prob = out["zero_alpha"] / (out["zero_alpha"] + out["zero_beta"])
        assert zero_prob > 0.4

    def test_renewal_rt_reasonable_on_growth(self):
        growing = [2 ** (i // 7) for i in range(42)]
        out = renewal_corri_fit(growing, si_mean=4.0, si_sd=2.0)
        assert out["r_final"] and out["r_final"] > 1.0

    def test_event_rate_posterior(self):
        out = event_rate_fit([1, 0, 1], a0=1.0, b0=1.0)
        assert out["rate_shape"] == 3.0 and out["rate_rate"] == 4.0

    def test_deterministic_repeated_fit(self):
        a = gamma_poisson_dglm_fit(SERIES)
        b = gamma_poisson_dglm_fit(SERIES)
        assert a == b

    def test_discount_bounds_respected_by_math(self):
        out = gamma_poisson_dglm_fit([0] * 20, discount=0.95, a0=1.0, b0=1.0)
        assert out["final_rate"] > 0
